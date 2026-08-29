# Design: SH-15/SH-20/SH-24/SH-26 — dbt project setup + SQL models (thin slice)

Status: **Frozen** — ready for `Developer-agent`
Branch: `feature/SH-2-15-20-24-26-dbt-scaffold-consumption` (already checked out off `main`, includes SH-11's merged RAW DDL/load + `manage.py`)
Traces to: `docs/05-Epics.md` §4.3, FR-PL-01/02/03/05/08, `docs/04-1-LLD.md`, `docs/04-3-LLD.md`

---

## 1. Problem & scope

Four tightly-coupled stories, one Definition of Done: **`dbt run && dbt test` succeeds end-to-end on the thin dataset; `CONS.FCT_SENSOR_READING` has real rows queryable in Snowsight.**

| Story | Scope |
|---|---|
| SH-15 (S-DBT-1) | Scaffold the dbt project — `dbt init`, `profiles.yml`, schema/layer tags |
| SH-20 (S-DBT-2) | `STD.SENSOR_READING` dynamic table — leakage-safe ASOF joins |
| SH-24 (S-DBT-3) | `CONS.FCT_SENSOR_READING` pass-through + `CONS.DIM_EQUIPMENT` + `CONS.DIM_SENSOR_BASELINE` |
| SH-26 (S-DBT-4) | dbt tests (not_null/relationships) on key columns |

No implementation code is written by this design doc — that's `Developer-agent`'s job next.

---

## 2. Agreed decisions (from brainstorm)

| Decision | Resolution |
|---|---|
| **Snowflake connection/account** | **`snow-coco`** (original account, `HUFZQDD-UPC40501`) — same account all prior RAW work landed on. The newly-active `snow-co-cat-alyst` connection (`IQWYCFG-OAC98123`) is a *separate* concern: it's for driving the CoCo agent/CLI faster, not a new data environment. `profiles.yml` targets this account with **`authenticator: externalbrowser`** (user-confirmed) — a browser-based OAuth login, consistent in spirit with `manage.py`'s OAuth path, though dbt manages its own token/session separately from `snowflake-connector-python`'s `keyring` cache. |
| **dbt project location** | `dbt/` subdirectory at repo root (`dbt/dbt_project.yml`, `dbt/models/...`, `dbt/profiles.yml` or `~/.dbt/profiles.yml` per standard dbt convention) — mirrors `generator/` and `scripts/` each owning their own top-level directory, keeps dbt's file sprawl out of repo root. |
| **Raw sources** | Define a `sources.yml` in `dbt/models/raw/` (or `dbt/models/staging/`) covering `RAW.EQUIPMENT`, `RAW.SENSOR_READING`, `RAW.CMMS_LOG` — standard dbt practice, enables lineage graph and future freshness checks even though not exercised yet. |
| **`CONS.DIM_SENSOR_BASELINE` seeding** | **3 rows only** (CNC Boring × VIBRATION/TEMPERATURE/RPM), matching the thin dataset's actual scope exactly. Values come straight from `docs/04-2-LLD.md` §2 / `generator/thin_sensor_generator.py`'s `SENSOR_BASELINES`: `VIBRATION` mean 2.5 / std 0.3, `TEMPERATURE` mean 45.0 / std 3.0, `RPM` mean 2400.0 / std 150.0. The other 6 rows (CNC Milling, CNC Horizontal — values already known from Module 2 §2 but no RAW data exists for them yet) are added later in EPIC-FULLDATA when those machines' RAW data actually lands — do not pre-seed them now. |
| **`target_lag`** | **1 hour** for both `STD.SENSOR_READING` and `CONS.FCT_SENSOR_READING`, as a deliberate, documented **temporary deviation** from FR-PL-04a's 15-minute spec — credit-conservative for this dev/thin pass where ticks are only injected manually/rarely, not on a real cadence yet. **Must be reverted to 15 minutes before the demo cadence (FR-PL-04, FR-CC-06) actually matters** — flag this explicitly in `schema.yml`/model config comments and in `Documenter-agent`'s CHANGELOG entry so it isn't silently forgotten. |
| **`hours_since_last_service`** | Confirmed: will be `NULL` for every row in this thin pass. `RAW.CMMS_LOG` is created (SH-11) but empty — SH-13's thin generator produces no CMMS log data. `STD.SENSOR_READING`'s `LEFT ASOF JOIN` against an empty table naturally null-pads every row; no special-casing needed in the SQL. Documented as an **accepted thin-pass limitation**, resolved once EPIC-FULLDATA's generator populates `RAW.CMMS_LOG`. |
| **dbt test scope (SH-26)** | Minimal: `not_null` on key columns + one `relationships` test. Specifically (see §5 below for exact columns): `not_null` on `reading_id`/`equipment_id` (`STD.SENSOR_READING`, `CONS.FCT_SENSOR_READING`), `not_null` on `equipment_id` (`CONS.DIM_EQUIPMENT`, `CONS.DIM_SENSOR_BASELINE`), plus one `relationships` test: `CONS.FCT_SENSOR_READING.equipment_id` → `CONS.DIM_EQUIPMENT.equipment_id`. No `accepted_values` tests added in this thin pass. |
| **`manage.py` integration** | Wire dbt into `manage.py` as a new step. Fill `scripts/04_pipeline_run_phase1.sql`'s placeholder with a comment documenting the actual invocation (dbt isn't SQL, so this file becomes a documentation/reference stub, not executable SQL — matches how `01_setup.sql`'s header already documents `manage.py`'s wrapping). `manage.py up` gains a new step after `03_setup_raw_load.sql`: shell out to `dbt run` / `dbt test` inside `dbt/` (e.g. `subprocess.run(["dbt", "run"], cwd=DBT_DIR)` then `dbt test`), using the same `snow-coco` connection profile. This is pipeline-run script v1 (single-phase, thin) per `docs/05-Epics.md` §2 — later split into phase 1/2 by `S-OPS-PIPE-2` (EPIC-RUL) once model-inference tables exist. |

---

## 2a. Deviation from original design (2026-08-29): actual dbt scaffold

The user built the dbt scaffold directly (not via `Developer-agent`) before this section was written. Actual state, verified via `dbt debug` (already passing):

| Design doc said | Actually built |
|---|---|
| `dbt/` subdirectory | **`predictive_maintenance_dbt/`** at repo root |
| Project name `snowcomotive` | **`predictive_maintenance_dbt`** |
| `authenticator: externalbrowser` | **`authenticator: oauth_authorization_code`** (matches `snow-coco`'s `connections.toml` entry exactly) |
| `profiles.yml` at `~/.dbt/` (workspace-level, out of repo) | **`predictive_maintenance_dbt/profiles.yml`**, committed in-repo (no secrets present either way — `oauth_authorization_code` has none to store) |
| — | Profile target `schema: RAW`, `threads: 1`, `role: SNOWCOMOTIVE_ROLE`, `warehouse: SNOWCOMOTIVE_WH`, `database: SNOWCOMOTIVE` |

**All subsequent references in this doc to `dbt/...` paths and the `snowcomotive` project name should be read as `predictive_maintenance_dbt/...` and `predictive_maintenance_dbt`** — not re-written throughout for brevity, but this section is authoritative over any conflicting path/name mentioned elsewhere in this doc. The boilerplate `models/example/` (my_first_dbt_model.sql, my_second_dbt_model.sql, schema.yml) should be deleted as part of SH-15's actual model work, since it's just `dbt init`'s default scaffolding, not part of this design.

`dbt_utils` package: not added. This thin slice's tests are all native dbt tests (`not_null`/`relationships`), and no surrogate-key/other macro-dependent logic is needed — nothing in scope currently requires it.

---

## 3. SH-15 — dbt project scaffold

- `dbt/dbt_project.yml`: project name `snowcomotive`, `profile: snowcomotive`.
- `~/.dbt/profiles.yml` (standard out-of-repo dbt convention, user-confirmed — NOT `dbt/profiles.yml` at project root, even though no secrets are involved): target `snow-coco`, `type: snowflake`, `authenticator: externalbrowser`, `account`, `role: snowcomotive_role`, `warehouse: snowcomotive_wh`, `database: snowcomotive`, `schema: dbt_default` (schemas are overridden per-model via `+schema` config, not the profile default — see below). Document the expected file content as a copyable snippet in `dbt/README.md` since a fresh clone won't have this file.
- Model directory layout under `dbt/models/`:
  - `staging/` (or `raw/`) — `sources.yml` (RAW.EQUIPMENT/SENSOR_READING/CMMS_LOG).
  - `standardized/` — `std__sensor_reading.sql`, `std__equipment.sql` (Module 1 lists `STD.EQUIPMENT` as `table`, type/key conformance only — needed as the ASOF join's other side per Module 3 §1's `raw.equipment e` reference; build as a thin pass-through `table` model).
  - `consumption/` — `cons__fct_sensor_reading.sql`, `cons__dim_equipment.sql`, `cons__dim_sensor_baseline.sql` (seed, see §5).
- **Schema/layer tagging (FR-PL-08)**: each model's `dbt_project.yml` or model-level config sets `+schema` to `std`/`cons` (Raw is never a dbt model — FR-PL-00) and a `tags: ['standardized']` / `tags: ['consumption']` config, so `dbt run --exclude tag:inference+` (used later once inference models exist) can select cleanly by layer. Raw sources are declared via `sources.yml`, not modeled.
- Custom schema macro: dbt by default prefixes custom schemas with the target schema (e.g. `dbt_default_std`) — override `generate_schema_name` in `dbt/macros/generate_schema_name.sql` to return the schema literally (`std`, `cons`), matching the already-created `snowcomotive.std`/`snowcomotive.cons` schemas from `01_setup.sql`. This is a standard, minimal dbt scaffolding macro — not a modeling decision, but must exist or every model lands in the wrong schema.

---

## 4. SH-20 — `STD.SENSOR_READING`

Per `docs/04-3-LLD.md` §1, verbatim SQL shape (materialized as `dynamic_table`, `target_lag='1 hour'` per §2 above):

```sql
{{ config(materialized='dynamic_table', target_lag='1 hour', warehouse='snowcomotive_wh', tags=['standardized']) }}

SELECT
    r.reading_id,
    r.equipment_id,
    r.reading_ts,
    r.sensor_type,
    r.reading_value,
    DATEDIFF('hour', m.event_end_ts, r.reading_ts)    AS hours_since_last_service,
    DATEDIFF('hour', e.commissioned_ts, r.reading_ts) AS hours_since_install
FROM {{ source('raw', 'sensor_reading') }} r
JOIN {{ ref('std__equipment') }} e
    ON e.equipment_id = r.equipment_id
LEFT ASOF JOIN {{ source('raw', 'cmms_log') }} m
    MATCH_CONDITION (r.reading_ts > m.event_end_ts)
    ON r.equipment_id = m.equipment_id
```

- `std__equipment.sql`: thin pass-through `table` model, type/key conformance only, selecting from `{{ source('raw', 'equipment') }}`.
- No calendar join (Module 3 §1 — generator never emits non-operating-period ticks, so `is_working_period` would always be `TRUE`; dead weight, correctly omitted).
- **Invariant to preserve** (flag for `Reviewer-agent`): the `ASOF JOIN`'s `MATCH_CONDITION (r.reading_ts > m.event_end_ts)` must stay strictly-greater-than — this is FR-PL-03's leakage-safety requirement. Do not weaken to `>=`.

---

## 5. SH-24 — Consumption lean fact + dims

**`cons__fct_sensor_reading.sql`** (`dynamic_table`, `target_lag='1 hour'`, pass-through per Module 3 §2):

```sql
{{ config(materialized='dynamic_table', target_lag='1 hour', warehouse='snowcomotive_wh', tags=['consumption']) }}

SELECT
    equipment_id, reading_ts, sensor_type, reading_value,
    hours_since_last_service, hours_since_install
FROM {{ ref('std__sensor_reading') }}
```

Note: Module 1's column list for `CONS.FCT_SENSOR_READING` omits `reading_id`, but SH-26's not_null test needs a stable key column. **Resolution**: keep `reading_id` in the `SELECT` (harmless additive column, matches the Standardized grain 1:1) so the not_null/uniqueness test has something to anchor to — this is a minor, non-breaking addition to Module 1's spec; flag it to `Documenter-agent` to reconcile in `schema.yml`.

**`cons__dim_equipment.sql`** (`table`, thin pass-through of `STD.EQUIPMENT` — 1 row for the thin dataset, no transformation beyond direct column selection). **Corrected 2026-08-29**: originally specified (and initially implemented) as reading directly from `{{ source('raw', 'equipment') }}`, which skips the Standardized layer entirely — a real layering violation of this project's own Raw→Standardized→Consumption principle (FR-PL-02/05). Consumption models must build on Standardized, not bypass it. Fixed to reference `{{ ref('std__equipment') }}` instead — caught by the user during review, not by any agent in the Design→Developer→Reviewer chain, worth noting as a gap in that chain's checks:

```sql
{{ config(materialized='table', tags=['consumption']) }}

SELECT equipment_id, equipment_name, line_name, product_id, variant,
       is_sensor_enabled, throughput_units_per_hour, commissioned_ts
FROM {{ ref('std__equipment') }}
```

**`cons__dim_sensor_baseline.csv`** (dbt **seed**, static reference data — 3 rows, CNC Boring only, per §2 above):

```csv
equipment_id,sensor_type,baseline_mean,baseline_std
CNC_BORING,VIBRATION,2.5,0.3
CNC_BORING,TEMPERATURE,45.0,3.0
CNC_BORING,RPM,2400.0,150.0
```

Seeded via `dbt seed`, landing as `CONS.DIM_SENSOR_BASELINE` (`+schema: cons` config applied to seeds the same way as models, via `dbt_project.yml`'s `seeds:` block).

Out of scope for SH-24: `CONS.DIM_PRODUCT` — not cited by any of these 4 stories' Epics entries (S-DBT-3 only lists `DIM_EQUIPMENT`/`DIM_SENSOR_BASELINE`); deferred to whichever future story actually needs product-grain rollups.

---

## 6. SH-26 — dbt tests

In each model's `schema.yml`:

| Model | Column | Test |
|---|---|---|
| `std__sensor_reading` | `reading_id` | `not_null` |
| `std__sensor_reading` | `equipment_id` | `not_null` |
| `cons__fct_sensor_reading` | `reading_id` | `not_null` |
| `cons__fct_sensor_reading` | `equipment_id` | `not_null` |
| `cons__fct_sensor_reading` | `equipment_id` | `relationships` → `cons__dim_equipment.equipment_id` |
| `cons__dim_equipment` | `equipment_id` | `not_null` |
| `cons__dim_sensor_baseline` | `equipment_id` | `not_null` |
| `cons__dim_sensor_baseline` | `sensor_type` | `not_null` |

This satisfies FR-PL-08 ("at least one dbt test on key columns") for every model touched by this thin slice without over-building.

---

## 7. `manage.py` / pipeline-run script wiring

- `scripts/04_pipeline_run_phase1.sql` becomes a **documentation stub** (not executable SQL) noting: "dbt is not SQL — actual invocation is `manage.py`'s new step, shelling out to `dbt run`/`dbt test` inside `dbt/`. See `manage.py` and this story's design doc." Keep the file for the numbering/traceability convention in `scripts/README.md`, but its `Status` row updates from "Not built" to "Built (v1, thin, no phase split yet)".
- `manage.py`: add a `run_dbt()` step, called after `run_sql_file(cur, SCRIPTS_DIR / "03_setup_raw_load.sql")` inside `run_up()`. Implementation shape: `subprocess.run(["dbt", "run"], cwd=REPO_ROOT / "dbt", check=True)` then `subprocess.run(["dbt", "test"], cwd=REPO_ROOT / "dbt", check=True)`. Uses the `dbt/profiles.yml` target (`snow-coco`), not the Snowflake connector session `run_up()` already holds open — dbt manages its own connection.
- `scripts/README.md`'s run-order table and status column get updated to reflect this (04 now "Built").

---

## 8. Out of scope for this story

- Model training/inference (`FEAST` schema, IsolationForest) — SH-2-15..26 stop at Consumption; `FEAST` and inference dynamic tables are EPIC-SKELETON §4.4 (S-MODEL-1/2/3), a separate story.
- `CONS.DIM_PRODUCT`, `CONS.FCT_MAINTENANCE_EVENT`, `CONS.FCT_ORDER`, `CONS.FCT_INVENTORY_*`, `CONS.FCT_OEE` — not part of this thin slice; those tables' source RAW data doesn't exist yet either (order/inventory/calendar generation is EPIC-FULLDATA).
- `STD.CMMS_LOG`, `STD.SALES_ORDER`, `STD.INVENTORY_FG_SNAPSHOT`, `STD.SPARE_PART_SNAPSHOT`, `STD.CALENDAR` — same reason.
- Reverting `target_lag` to 15 minutes — explicitly deferred, tracked as a flagged item (see §2).
- Seeding the remaining 6 `DIM_SENSOR_BASELINE` rows (CNC Milling, CNC Horizontal) — deferred to EPIC-FULLDATA.
- Splitting the pipeline-run script into phase 1 / training / phase 2 — that's `S-OPS-PIPE-2` (EPIC-RUL), once inference models exist and reference-by-name-at-creation-time ordering actually matters.

---

## 9. Open items for `Developer-agent` — RESOLVED by user (2026-08-29)

1. ~~Exact `dbt-snowflake` profile auth method~~ — **RESOLVED**: `authenticator: externalbrowser` in `dbt/profiles.yml`'s target block (targets the `snow-coco` account, browser-based OAuth like `manage.py`'s OAuth path, though dbt's own token caching behavior — separate from `snowflake-connector-python`'s `keyring` cache — should be confirmed at first run; may prompt a browser login on the first `dbt run`).
2. ~~`profiles.yml` location~~ — **RESOLVED**: lives at the standard dbt convention location, `~/.dbt/profiles.yml` ("workspace level", i.e. outside the repo, not `dbt/profiles.yml` at project root) — no secrets involved either way (externalbrowser has none to store), but the user wants the standard out-of-repo convention followed regardless. Developer-agent must create/update `~/.dbt/profiles.yml` directly (not commit a project-local file) and should note this in `dbt/README.md` or `dbt_project.yml`'s header comment so it's discoverable (a fresh clone needs this file created manually — document the expected `profiles.yml` content as a copyable snippet).
3. `generate_schema_name` macro exact form — still to verify against the installed `dbt-snowflake` version's default behavior at build time.
4. ~~Confirm `dbt-snowflake` is added to `pyproject.toml`~~ — **RESOLVED, already done**: `dbt-snowflake>=1.12.0` is already present in `pyproject.toml`/`uv.lock` — no `uv add` needed.
5. `Reviewer-agent` should explicitly re-verify the `MATCH_CONDITION (r.reading_ts > m.event_end_ts)` strict inequality survives implementation unchanged (FR-PL-03 leakage-safety invariant) and that `hours_since_last_service` is confirmed NULL for all rows against the actual thin dataset (not just assumed).

---

## 10. Deviations found during implementation (2026-08-29)

Two additional real deviations from this doc's §4/§5 SQL, discovered during implementation and independently re-verified by `Reviewer-agent`. Documented explicitly per this project's convention (see §2a above for the precedent) — not silently folded into the "as-built" state.

### 10.1 `dynamic_table` config key: `warehouse` → `snowflake_warehouse`

| | |
|---|---|
| **Design doc said** (§4, §5) | `{{ config(materialized='dynamic_table', target_lag='1 hour', warehouse='snowcomotive_wh', ...) }}` |
| **Actually built** | `{{ config(materialized='dynamic_table', target_lag='1 hour', schema='std'\|'cons', snowflake_warehouse='snowcomotive_wh', ...) }}` — key is **`snowflake_warehouse`**, not `warehouse` |
| **Why** | The installed `dbt-snowflake` version's `dynamic_table` materialization does not route a plain `warehouse` config key onto the generated `CREATE DYNAMIC TABLE ... WAREHOUSE = ...` clause. |
| **Verified how** | `SHOW DYNAMIC TABLES` — the executed DDL only showed the correct `warehouse = SNOWCOMOTIVE_WH` after switching the config key to `snowflake_warehouse`; with the doc's literal `warehouse=` key, the generated DDL silently fell back to a different warehouse (no error raised — a silent-drift risk, which is exactly why this needed independent DDL verification rather than trusting `dbt run`'s exit code alone). |
| **Files** | `predictive_maintenance_dbt/models/standardized/std__sensor_reading.sql:1`, `predictive_maintenance_dbt/models/consumption/cons__fct_sensor_reading.sql:1` |
| **Risk** | None once fixed — purely a config-key naming deviation, no correctness impact on row data. |

### 10.2 `ASOF JOIN` grammar: `LEFT ASOF JOIN` → bare `ASOF JOIN`

| | |
|---|---|
| **Design doc said** (§4) | `LEFT ASOF JOIN {{ source('raw', 'cmms_log') }} m ...` |
| **Actually built** | bare `ASOF JOIN {{ source('raw', 'cmms_log') }} m ...` — no `LEFT`/`INNER` prefix |
| **Why** | This Snowflake account's `ASOF JOIN` grammar **rejects an explicit `LEFT`/`INNER` prefix entirely** (a parse-time error, not a semantics difference) — verified empirically during implementation. |
| **Correctness impact** | **None.** Independently re-confirmed by `Reviewer-agent` against live data: this account's bare `ASOF JOIN` already null-pads unmatched left rows by default (LEFT-JOIN-outer semantics), so dropping the `LEFT` keyword does not change row-loss/leakage behavior. |
| **Verified how** | `RAW.CMMS_LOG` is empty (0 rows). `STD.SENSOR_READING` and `CONS.FCT_SENSOR_READING` both retain the full 4320/4320 rows from `RAW.SENSOR_READING`, with `hours_since_last_service` correctly `NULL` for every row. Zero rows silently dropped by the join — the outer-join-by-default behavior holds. |
| **Invariant preserved** | The strict `>` in `MATCH_CONDITION (r.reading_ts > m.event_end_ts)` (FR-PL-03 leakage-safety requirement, §4's flagged invariant) is unchanged — this deviation only concerns the `LEFT`/`INNER` prefix keyword, not the match condition itself. |
| **Files** | `predictive_maintenance_dbt/models/standardized/std__sensor_reading.sql:8-12,24` (in-code comment explains the deviation at the point of use) |
| **Portability caveat** | This is an *account-specific* grammar behavior, not necessarily standard Snowflake `ASOF JOIN` behavior across all accounts/versions — if this project is ever migrated to a different Snowflake account, this behavior should be re-verified rather than assumed to carry over. |

Cross-references added to `docs/04-3-LLD.md` (its `LEFT ASOF JOIN` snippet) and `docs/04-4-LLD.md` (its `LEFT ASOF JOIN` snippet, FEAST module — same account, same grammar constraint would apply if/when implemented) pointing back to this section.
