# Environment & Operations Scripts

Lifecycle scripts for the SnowComotive project, per FR-OPS-01 through 06 / LLD Module 10 / `docs/05-Epics.md` §2. Numbered by execution order. These are **versioned, living scripts** — extended in place as later Epics add components, not replaced with new files. See each file's header comment for its current scope and what's still pending.

| # | File | Stage | Status | Jira |
|---|---|---|---|---|
| 01 | `01_setup.sql` | Env spin-up (role/warehouse/db/schemas/stage) | **Built** (v0 scope: S-ENV-1/S-ENV-2) | SH-10, SH-14 (Done) |
| 02 | `02_setup_raw_ddl.sql` | RAW table DDL (RAW.EQUIPMENT/SENSOR_READING/CMMS_LOG + SALES_ORDER/INVENTORY_FG_SNAPSHOT/SPARE_PART_SNAPSHOT/CALENDAR) | **Built** (v2 — EPIC-FULLDATA tables added) | SH-11 (S-DATA-2), SH-34 (S-OPS-SETUP-2), SH-36 (S-DATA-8) |
| 03 | `03_setup_raw_load.sql` | Upload + load full generator output into all 7 RAW tables (RAW.CALENDAR full-replace) | **Built** (v2 — EPIC-FULLDATA tables + CMMS_LOG loading added) | SH-11 (S-DATA-2), SH-34 (S-OPS-SETUP-2), SH-36 (S-DATA-8) |
| 04 | `04_pipeline_run_phase1.sql` | dbt run — features (Raw→Std→Cons→FEAST) | Built (v2 — FEAST added, tag:inference+ phase split wired; SH-50 further splits out `feast__training_dataset_rul` to build after model training, see below) | SH-15, SH-20, SH-24, SH-26, SH-29, SH-50 |
| 05 | `05_train_models.sql` | Model training (IsolationForest) | Built (v1 — IsolationForest, via CREATE PROCEDURE + CALL) | SH-22 (IsolationForest), SH-44 (RUL, P1) |
| 06c | `06c_evaluate_iso_model.sql` | Model evaluation (IsolationForest) | **Built** (v1 — per-cycle catch rate/lead time excluding train/test-boundary-straddling cycles, 72h-window precision/recall/FPR, per-machine breakdown, missed-breakdown technician_notes cross-check, Registry metric logging via CREATE PROCEDURE + CALL; runs immediately after `05_train_models.sql`, before `feast__training_dataset_rul`) | SH-46 (S-RUL-4, scope extension) |
| 06 | `06_train_rul_model.sql` | Model training (RUL AFT) | **Built** (v1 — raw `xgboost.Booster`, `survival:aft`, via CREATE PROCEDURE + CALL; runs after `feast__training_dataset_rul` is built) | SH-44 (S-RUL-3) |
| 06b | `06b_evaluate_rul_model.sql` | Model evaluation (RUL AFT) | **Built** (v1 — concordance index via manual pairwise fallback, MAE/RMSE/median-AE on uncensored subset, per-row breakdown, outlier root-cause check, Registry metric logging via CREATE PROCEDURE + CALL; runs after `06_train_rul_model.sql`) | SH-46 (S-RUL-4) |
| 07 | `06_pipeline_run_phase2.sql` | dbt run — inference & downstream (`cons__fct_anomaly_result`, tag:inference) | **Built** | SH-23 |
| 08 | `07_post_setup.sql` | Semantic view + agent(s) + Streamlit deploy | **Built** (v1 — 8-entity semantic view w/ 1 verified query, Analyst-only `maintenance_supervisor_agent`, `streamlit_stage`; `CREATE STREAMLIT` itself + the app-file `PUT` live in `manage.py`'s `run_post_setup()`, not this file) | SH-30, SH-27, SH-28, SH-31, SH-33 |
| 09 | `manage.py demo inject-tick` / `reset-cursor` | Live tick injection (demo-only manual trigger) | **Built** — Python-only, direct synchronous PUT+COPY INTO per invocation; `08_demo.sql` kept only as a historical stub (never executed — see its header) | SH-41 (S-DATA-9) |
| 10 | `09_teardown.sql` | Full teardown (P3, deliberately last) | Not built | SH-68, SH-61 |

Run order: 01 → 02 → 03 → 04 → 05 → 06c → (dbt `feast__training_dataset_rul`) → 06 → 06b → (dbt phase2) → 07, then `manage.py demo inject-tick` on demand during a demo, 09 only when actually tearing down an environment. See `manage.py`'s own module docstring for the exact interleaved dbt/script sequence (`06c_evaluate_iso_model.sql` runs immediately after `05_train_models.sql`, needing only `isolation_forest_model` to exist — no dependency on `feast.training_dataset_rul` or `cons.fct_anomaly_result`, so it runs earlier than the RUL evaluation step, not grouped alongside it; `06_train_rul_model.sql` reads `feast.training_dataset_rul`, which itself depends on `isolation_forest_model` existing — so it must run strictly after that table's dbt build and strictly before the phase-2 `tag:inference+` dbt run; `06b_evaluate_rul_model.sql` must run strictly after `06_train_rul_model.sql`, needing `rul_aft_model` to exist, and strictly before the phase-2 dbt run).

**Primary invocation: `manage.py up` / `manage.py down`** (repo root, `typer` CLI). `up`
runs `01_setup.sql` as ACCOUNTADMIN (the role it creates doesn't exist yet to log in
as) → switches to `snowcomotive_role` for everything else (so objects it creates are
owned consistently with dbt's own connection) → generates the full dataset via
`generator/full_data_generator.py`'s `run_simulation()` (called in-process; produces
all 7 RAW-bound `output/*.parquet` files plus the trailing-30-day `output/live_ticks/`
files used by `manage.py demo inject-tick`) → `02_setup_raw_ddl.sql` →
`03_setup_raw_load.sql`, all through one `snow-coco` connector session (OAuth, token
cached via `keyring` — see `AGENTS.md` "Local dev environment"), then shells out to
`dbt seed` → `dbt run --exclude tag:inference+ --exclude feast__training_dataset_rul`
→ `05_train_models.sql` (own `snowcomotive_role` connector session, trains and
registers the IsolationForest model, SH-22) → `06c_evaluate_iso_model.sql` (own
`snowcomotive_role` connector session, evaluates `isolation_forest_model`
against the test split + `RAW.CMMS_LOG`, logs Registry metrics, SH-46 scope
extension) → `dbt run --select
feast__training_dataset_rul` (SH-50 — this model calls `MODEL(isolation_forest_model)`
directly, so it must build after training rather than in the phase-1 pass above) →
`dbt run --select tag:inference+` (`cons__fct_anomaly_result`,
tag `inference` — real inference work now, SH-23, confirmed incrementally
refreshing) → `dbt test` inside `predictive_maintenance_dbt/`
(its own committed `profiles.yml`, same `snow-coco` account, dbt manages its own
connection separately from the connector sessions above) → `run_post_setup()`
(own `snowcomotive_role` connector session: `07_post_setup.sql` — semantic
view + agent + `streamlit_stage` — then `PUT`s `oee_command_center_app/`'s
files to that stage and issues `CREATE OR REPLACE STREAMLIT` directly, SH-30/
27/28/31/33). `down` runs
`09_teardown.sql` as ACCOUNTADMIN (it drops `snowcomotive_role` itself).

**The `snow` CLI is not used in this project.** It was tried and dropped: the
MFA-authenticated `snow` CLI connection required a fresh TOTP passcode on every
call, which was rejected as a workflow — see
`docs/designs/1 - SH-2-11-upload-load-into-raw.md` §8 for the full writeup. Do
not run `snow sql -f` for these scripts; use `manage.py` instead, extending it
if a new script needs to join the sequence.

`manage.py up` always does full generation — `--seed` (default 42) and
`--now` (default today) anchor the generator's 3-year-back/8-week-forward
window, and `--reuse-dataset-path` skips generation if the given path already
has the expected output files. `generator/thin_sensor_generator.py` (SH-13's
original thin skeleton) is left untouched as a standalone reference script —
`manage.py up` no longer calls it. Running the generator manually is only
needed for standalone debugging outside `manage.py`:

```
uv run python -m generator.full_data_generator --seed 42 --output-dir ./output
```

## Local/ad hoc connections — use TWO connection profiles, not one

`manage.py` itself is unaffected by which role your connection defaults to —
every step above explicitly issues `USE ROLE ACCOUNTADMIN`/`USE ROLE
snowcomotive_role` in its own SQL, regardless of the connection's login role.
This section is only about **ad hoc queries run outside `manage.py`**
(Snowsight worksheets, `snowflake_sql_execute`, a debugging script, etc.).

This account's `chiraj` user has `DEFAULT_ROLE = ACCOUNTADMIN`. Snowflake's
`ACCOUNTADMIN` does **not** automatically inherit SELECT/USAGE access to
objects owned by another role — and everything this project creates (tables,
dynamic tables, models, the semantic view, the agent) is owned by
`snowcomotive_role`, per `01_setup.sql`'s grants. So an ad hoc query run
through the plain connection profile (which defaults to `ACCOUNTADMIN`) can
fail with a permission error even though the object clearly exists — hit for
real during SH-46's post-teardown verification (2026-09-26).

**Do not fix this by granting `ACCOUNTADMIN` broad access to `snowcomotive`
objects.** That defeats the whole point of having a dedicated least-privilege
role, and doubles the grant-maintenance burden for every future object type
(models, agents, semantic views each have their own grant hierarchy).

**Also do not just pin `role = "snowcomotive_role"` on the existing bootstrap
connection profile.** Snowflake validates the `role` field at connect time —
if you pin it on the *only* connection profile you have, that profile becomes
unusable at exactly the moments you need it most: before `01_setup.sql` has
ever run (the role doesn't exist yet) and immediately after `manage.py down`
(the role was just dropped). `manage.py up`/`down` need a connection that can
always get in, regardless of whether `snowcomotive_role` currently exists.

**Instead, keep two separate connection profiles** in
`~/.snowflake/connections.toml`:

```toml
# Bootstrap connection -- used by manage.py up/down. No role pinned, so it
# can always connect (defaults to the account's own default role) even
# before snowcomotive_role exists or right after a teardown.
[snow-co-cat-alyst]
account = "IQWYCFG-OAC98123"
user = "chiraj"
authenticator = "snowflake"
password = "..."

# Day-to-day ad hoc connection -- role pinned, only usable once the
# environment has actually been set up. Use this one for Snowsight-style
# ad hoc queries so you never have to remember `USE ROLE` manually.
[snow-co-cat-alyst-snowcomotive]
account = "IQWYCFG-OAC98123"
user = "chiraj"
authenticator = "snowflake"
password = "..."
role = "snowcomotive_role"
```

Use the first profile only for `manage.py up`/`down` and anything that must
work regardless of environment state. Use the second profile for everything
else — ad hoc SQL, debugging, exploratory queries — so `snowcomotive_role` is
active automatically without a manual `USE ROLE` step.
