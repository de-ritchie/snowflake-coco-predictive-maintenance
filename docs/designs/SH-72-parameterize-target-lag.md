# Design: Parameterize dynamic table `target_lag` (leaf-only param + DOWNSTREAM cascade)

Status: **Frozen**
**Story**: SH-72 (S-OPS-HARDEN-3)
**Traces to**: [docs/03-HLD.md](../03-HLD.md), [docs/04-4-LLD.md](../04-4-LLD.md) (dynamic-table refresh-mode/lag constraints), Snowflake product docs "Quick-start best practices for dynamic tables" and "Set the target lag for a dynamic table" (docs.snowflake.com/en/user-guide/dynamic-tables/)

---

## 1. Problem statement

All 6 dynamic tables in the project's single linear refresh chain are currently hardcoded to `target_lag='1 hour'` independently:

```
std__sensor_reading → cons__fct_sensor_reading → feast__fct_sensor_features_inference
  → cons__fct_anomaly_result → cons__fct_rul_prediction → cons__fct_priority_score
```

Verified live (this session) — every one of the 6 model files still has `target_lag='1 hour'` in its `config()` block, matching the story's assumption exactly; nothing has changed since the story was filed.

This has two problems Snowflake's own dynamic-table best-practice docs call out directly:
1. **Wasteful**: setting a lag independently on every table in a chain means Snowflake schedules refreshes for *each* table on its own cadence, when only the leaf's actual freshness requirement matters — the docs' stated best practice is to set an explicit `target_lag` only on the table(s) actually queried by something outside the pipeline, and `target_lag=DOWNSTREAM` on every purely-intermediate table, letting Snowflake derive their refresh cadence from the leaf.
2. **No operator control**: there is currently no way to speed up or slow down the whole chain's refresh cadence (e.g. for a faster demo) without editing 6 files by hand.

---

## 2. Decision table

| Table | Role | New `target_lag` | Mechanism |
|---|---|---|---|
| `std__sensor_reading` | intermediate | `DOWNSTREAM` | hardcoded literal |
| `cons__fct_sensor_reading` | intermediate | `DOWNSTREAM` | hardcoded literal |
| `feast__fct_sensor_features_inference` | intermediate | `DOWNSTREAM` | hardcoded literal |
| `cons__fct_anomaly_result` | intermediate | `DOWNSTREAM` | hardcoded literal |
| `cons__fct_rul_prediction` | intermediate | `DOWNSTREAM` | hardcoded literal |
| `cons__fct_priority_score` | **leaf** (only table queried outside this dbt-ref chain — semantic view + Overview page) | `var('target_lag')`, default `'1 hour'` | dbt var, `DYNAMIC_TABLE_TARGET_LAG` env var |

---

## 3. Verification findings (the "don't just assume it works" checklist)

### 3.1 `ref()`-usage check across the whole `models/` tree — the most important check, done exhaustively

Grepped every `ref('std__sensor_reading')`, `ref('cons__fct_sensor_reading')`, `ref('feast__fct_sensor_features_inference')`, `ref('cons__fct_anomaly_result')`, `ref('cons__fct_rul_prediction')` across `predictive_maintenance_dbt/models/`. Results:

| Upstream table | Every `ref()` found | Verdict |
|---|---|---|
| `std__sensor_reading` | `cons__fct_sensor_reading` (1 consumer) | single path, safe for `DOWNSTREAM` |
| `cons__fct_sensor_reading` | `feast__fct_sensor_features_inference` **and** `feast__fct_sensor_features_train` (2 refs) | see 3.1.1 below — safe, but for a nuanced reason |
| `feast__fct_sensor_features_inference` | `cons__fct_anomaly_result` (1 consumer) | single path, safe for `DOWNSTREAM` |
| `cons__fct_anomaly_result` | `cons__fct_rul_prediction` (1 consumer) | single path, safe for `DOWNSTREAM` |
| `cons__fct_rul_prediction` | `cons__fct_priority_score` (2 refs, same model — `latest_rul` CTE + `rul_cap` CTE) | single consumer *model*, safe for `DOWNSTREAM` |

#### 3.1.1 `cons__fct_sensor_reading` has two `ref()`s — checked, not a problem

`feast__fct_sensor_features_train.sql` also calls `ref('cons__fct_sensor_reading')`, alongside `feast__fct_sensor_features_inference.sql`. This looked like it might break the "single linear downstream path" assumption DOWNSTREAM depends on. Read the file directly: `feast__fct_sensor_features_train` is `materialized='table'` (a **plain table**, not `dynamic_table`) — "Rebuilt as its own explicit pipeline step (FR-OPS-02a, scheduled Task), never incrementally — training needs a fixed point-in-time input" (its own header comment). Snowflake's `DOWNSTREAM` target-lag derivation only considers the **dynamic-table dependency graph** — a plain `table` materialization has no `target_lag`/refresh schedule at all and is rebuilt only when `dbt run` executes it (during `manage.py up`'s phase-1 step), never by Snowflake's own refresh scheduler. So this second `ref()` does not add a second dynamic-table consumer path — `DOWNSTREAM` on `cons__fct_sensor_reading` is still correct.

#### 3.1.2 Direct external consumers of two of the "5 intermediate" tables — a real nuance, not a blocker

Two of the five tables slated for `DOWNSTREAM` are also queried **directly**, outside the dbt-ref DAG entirely, by human-facing surfaces:

- `cons__fct_sensor_reading` — exposed as the `sensor_reading` entity in the semantic view (`scripts/07_post_setup.sql:74`) and queried directly by the Overview page's sensor-detail chart (`oee_command_center_app/pages/1_Overview.py:130`, comment: "Decision #8: sensor-detail chart reads cons.cons__fct_sensor_reading").
- `cons__fct_anomaly_result` — exposed as the `anomaly_result` entity in the semantic view (`scripts/07_post_setup.sql:77`) and queried directly by the Overview page's anomaly badges (`1_Overview.py:66,71,79,173`).

**This does not invalidate `DOWNSTREAM` as the correct choice.** Snowflake's `DOWNSTREAM` lag derivation is purely a function of the dynamic-table dependency graph (what other dynamic tables `ref()` this one) — it has no visibility into, and is not affected by, ad hoc `SELECT` queries issued by a Streamlit app or a semantic view against whatever data is currently materialized. Those surfaces will simply see the table's current state at query time, exactly as they do today; `DOWNSTREAM` only changes *how Snowflake schedules the refresh that produces that state*, not who is allowed to read it.

What **is** worth stating explicitly for the record: with this change, the refresh cadence of these two dashboard-facing tables becomes coupled to the leaf's `target_lag` value, not an independently-tunable value of their own. Today they're already coupled (all 6 are hardcoded to the same `'1 hour'` literal), so this preserves existing behavior at the default. If the operator ever raises `--target-lag` above `1 hour` for cost reasons, the sensor-detail chart and anomaly badges would also become slower to refresh — an acceptable, intended trade-off (one knob controls the whole pipeline's freshness, dashboards included), not a regression, but flagged here so it's a known consequence rather than a surprise.

No table's `ref()`s cross outside this one linear chain into a second dynamic-table consumer. **The single most important check requested passes cleanly** — `DOWNSTREAM` is correct for all 5 intermediate tables.

### 3.2 dbt-snowflake `var()` support for `target_lag` in `config()`

`pyproject.toml`/`uv.lock` pin `dbt-snowflake>=1.12.0` (`uv.lock:770`, resolved version present in the lockfile). `target_lag` is a plain string-valued config key on the `dynamic_table` materialization (no special Jinja-parsing restriction is documented for it, unlike e.g. keys that must resolve at parse-time for DDL structure such as column lists) — the project already proves `var()` resolves correctly into a similarly free-form string config *pattern* elsewhere (`oee_performance_pct`/`oee_quality_pct` consumed inside model SQL bodies via `{{ var('oee_performance_pct') }}`, `dbt_project.yml:44-45`), but that is a different injection point (inside a `SELECT`, not inside a `config()` call argument). No existing model in this project currently passes a `var()` result into `target_lag` specifically, so this exact combination has no precedent in this codebase.

**Flag for Developer-agent**: this should be confirmed empirically on the first `dbt run` after the change (`dbt run --select cons__fct_priority_score` in isolation is the cheapest way to check) — if `var()` doesn't resolve inside `target_lag='{{ var(...) }}'` cleanly for some adapter-specific reason, the fallback is the same env-var pattern with `{{ env_var(...) }}` called directly inside the `config()` block instead of going through `var()` first (i.e. skip the `dbt_project.yml` vars indirection and inline `target_lag="{{ env_var('DYNAMIC_TABLE_TARGET_LAG', '1 hour') }}"` straight into `cons__fct_priority_score.sql`'s own `config()`). Not expected to be needed — flagged per the story's explicit instruction not to just assert this works, not because there's live evidence of a problem.

### 3.3 `manage.py post-setup` / other commands needing the env var

Read `manage.py` in full. Only `run_up()` invokes the three dbt-shelling functions (`run_dbt_phase1()`, `run_dbt_training_dataset_rul()`, `run_dbt_phase2_and_test()`) that would rebuild these 6 dynamic tables from a `target_lag` config change. `run_post_setup()` (the `post-setup` command) only runs `scripts/07_post_setup.sql` (semantic view/agent DDL) + the Streamlit file `PUT`/`CREATE STREAMLIT` — it never shells out to `dbt run`, so it does not need the env var threaded. No other command in the file touches dbt. **Confirmed: only `run_up()` needs the one-line env var set.**

### 3.4 Real minimum `target_lag`

Confirmed via Snowflake product docs this session: **60 seconds** is the actual minimum, not 30 seconds. Design does not hardcode a faster default — `'1 hour'` stays the default, preserving current behavior when `--target-lag` isn't passed.

---

## 4. Exact changes, per file

### 4.1 `predictive_maintenance_dbt/dbt_project.yml`

Add one var, following the exact existing `oee_performance_pct`/`oee_quality_pct` pattern (same file, same section):

```yaml
# CONS.FCT_OEE constants (FR-PL-07, BRD assumption 3) -- same value applied
# to every line, env-var overridable per run without a code change.
vars:
  oee_performance_pct: "{{ env_var('OEE_PERFORMANCE_PCT', '0.98') }}"
  oee_quality_pct: "{{ env_var('OEE_QUALITY_PCT', '0.97') }}"
  # Dynamic-table refresh cadence for the pipeline's one leaf table
  # (cons__fct_priority_score) -- the 5 upstream tables are all
  # target_lag=DOWNSTREAM and derive their own cadence from this value
  # (SH-72). Snowflake's real minimum is 60 seconds.
  target_lag: "{{ env_var('DYNAMIC_TABLE_TARGET_LAG', '1 hour') }}"
```

### 4.2 `predictive_maintenance_dbt/models/consumption/cons__fct_priority_score.sql`

Change the `config()` block's `target_lag` value and update the now-stale header comment (it currently says target_lag is 1 hour "not the LLD's 15 minutes" *because* `cons__fct_rul_prediction` is "temporarily pinned to 1 hour" — that reasoning no longer applies once the 5 upstream tables are `DOWNSTREAM`, since DOWNSTREAM tables have no fixed lag of their own to be "pinned" to):

Before:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}
```
```sql
-- target_lag is 1 hour, not the LLD's 15 minutes -- Snowflake requires a
-- dynamic table's lag to be >= its dependencies' own lag, and
-- cons__fct_rul_prediction is itself temporarily pinned to 1 hour (see that
-- model's own config comment) instead of the 15-minute demo-cadence spec.
```

After:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag=var('target_lag'),
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='auto',
    initialize='on_create',
    tags=['inference']
) }}
```
```sql
-- target_lag is parameterized (dbt var `target_lag`, dbt_project.yml,
-- default '1 hour') -- this is the pipeline's one leaf table (the only one
-- of the 6-table chain queried by anything outside this dbt-ref DAG: the
-- semantic view + Overview page), so it's the only one that needs an
-- explicit, operator-tunable lag. The 5 upstream tables are all
-- target_lag=DOWNSTREAM and derive their own refresh cadence from this
-- value automatically (Snowflake's own documented best practice for a
-- single linear dynamic-table chain; SH-72). Set via
-- `manage.py up --target-lag <value>` (e.g. '1 minute' for a faster demo
-- cadence) -- Snowflake's real minimum is 60 seconds.
```

### 4.3 The 5 intermediate models — `target_lag=DOWNSTREAM` (hardcoded literal, no var)

**`predictive_maintenance_dbt/models/standardized/std__sensor_reading.sql`**

Before (`config()` line + header comment):
```sql
{{ config(materialized='dynamic_table', target_lag='1 hour', schema='std', snowflake_warehouse='snowcomotive_wh', tags=['standardized'], immutable_where='reading_ts < DATEADD(hour, -1, CURRENT_TIMESTAMP())') }}

-- Leakage-safe joins (FR-PL-02, FR-PL-03). NOTE: target_lag is 1 hour here,
-- a deliberate temporary deviation from FR-PL-04a's 15-minute spec for this
-- credit-conservative thin/dev pass -- revert to 15 minutes before the demo
-- cadence (FR-PL-04, FR-CC-06) actually matters. See design doc
-- docs/designs/2 - SH-2-15-20-24-26-dbt-scaffold-consumption.md section 2.
```

After:
```sql
{{ config(materialized='dynamic_table', target_lag='DOWNSTREAM', schema='std', snowflake_warehouse='snowcomotive_wh', tags=['standardized'], immutable_where='reading_ts < DATEADD(hour, -1, CURRENT_TIMESTAMP())') }}

-- Leakage-safe joins (FR-PL-02, FR-PL-03). target_lag is DOWNSTREAM (SH-72)
-- -- this is a purely intermediate table in the 6-table dynamic-table chain
-- (std -> cons.sensor_reading -> feast.inference -> cons.anomaly_result ->
-- cons.rul_prediction -> cons.priority_score); Snowflake derives its refresh
-- cadence from the chain's one leaf (cons__fct_priority_score's parameterized
-- target_lag) automatically. No longer independently pinned to 1 hour.
```

**`predictive_maintenance_dbt/models/consumption/cons__fct_sensor_reading.sql`**

Before:
```sql
{{ config(materialized='dynamic_table', target_lag='1 hour', schema='cons', snowflake_warehouse='snowcomotive_wh', tags=['consumption'], immutable_where='reading_ts < DATEADD(hour, -1, CURRENT_TIMESTAMP())') }}
```
After:
```sql
{{ config(materialized='dynamic_table', target_lag='DOWNSTREAM', schema='cons', snowflake_warehouse='snowcomotive_wh', tags=['consumption'], immutable_where='reading_ts < DATEADD(hour, -1, CURRENT_TIMESTAMP())') }}
```
No header-comment change needed here — this file's existing comments don't claim a "temporarily pinned" constraint (only `std__sensor_reading`, `feast__fct_sensor_features_inference`, `cons__fct_priority_score` do, per the story's own header-comment-cleanup list). Leave as-is.

**`predictive_maintenance_dbt/models/feast/feast__fct_sensor_features_inference.sql`**

Before:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    tags=['feast']
) }}

-- Always-fresh feature table (FR-FS-01, Module 4 §2). target_lag is
-- temporarily 1 hour, matching std__sensor_reading/cons__fct_sensor_reading's
-- same credit-conservative thin/dev deviation from the 15-minute demo spec.
-- refresh_mode is pinned to INCREMENTAL (not left on AUTO) so CREATE fails
-- loudly if the macro ever stops being incrementally maintainable, instead of
-- silently falling back to a full rescore that would break the "predict only
-- the new tick" invariant this table exists to guarantee (Module 3 §3, Module 4 §3).
```

After:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='DOWNSTREAM',
    schema='feast',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    tags=['feast']
) }}

-- Always-fresh feature table (FR-FS-01, Module 4 §2). target_lag is
-- DOWNSTREAM (SH-72) -- purely intermediate in the 6-table dynamic-table
-- chain; cadence derives from cons__fct_priority_score's parameterized lag.
-- refresh_mode is pinned to INCREMENTAL (not left on AUTO) so CREATE fails
-- loudly if the macro ever stops being incrementally maintainable, instead of
-- silently falling back to a full rescore that would break the "predict only
-- the new tick" invariant this table exists to guarantee (Module 3 §3, Module 4 §3).
```

**`predictive_maintenance_dbt/models/consumption/cons__fct_anomaly_result.sql`**

Before:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    initialize='on_create',
    tags=['inference']
) }}
```
After:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='DOWNSTREAM',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    initialize='on_create',
    tags=['inference']
) }}
```
No header-comment change needed — this file's existing comments don't claim a "temporarily pinned" constraint either.

**`predictive_maintenance_dbt/models/consumption/cons__fct_rul_prediction.sql`**

Before:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='1 hour',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    initialize='on_create',
    tags=['inference']
) }}
```
After:
```sql
{{ config(
    materialized='dynamic_table',
    target_lag='DOWNSTREAM',
    schema='cons',
    snowflake_warehouse='snowcomotive_wh',
    refresh_mode='incremental',
    initialize='on_create',
    tags=['inference']
) }}
```
No header-comment change needed for the "temporarily pinned" wording — this file doesn't have that phrase itself, but `cons__fct_priority_score.sql`'s own comment (§4.2 above) references it as "cons__fct_rul_prediction is itself temporarily pinned to 1 hour (see that model's own config comment)" — that cross-reference is corrected as part of the `cons__fct_priority_score.sql` edit in §4.2, not here.

### 4.4 `manage.py`

Add a `--target-lag` option on `up` (default `"1 hour"`) and set the env var once inside `run_up()`, before any dbt-shelling call. Exact insertion points, verified against the live file:

**`run_up()` signature + first line of body** (`manage.py:326`):
```python
def run_up(seed: int, now: str, reuse_dataset_path: str | None, target_lag: str) -> None:
    # DYNAMIC_TABLE_TARGET_LAG (SH-72) -- read by dbt_project.yml's
    # `target_lag` var, consumed only by cons__fct_priority_score.sql (the
    # pipeline's one leaf dynamic table; the other 5 are target_lag=
    # DOWNSTREAM and need no env var). Set once here, before any of this
    # function's three dbt-shelling calls (run_dbt_phase1(),
    # run_dbt_training_dataset_rul(), run_dbt_phase2_and_test()) -- each
    # inherits this process's env automatically via subprocess.run's default
    # env=None passthrough, so no --vars flag is threaded through any of the
    # three call sites individually.
    os.environ["DYNAMIC_TABLE_TARGET_LAG"] = target_lag
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="ACCOUNTADMIN")
    ...  # rest of function body unchanged
```

**`up` command** (`manage.py:612-625`):
```python
@app.command("up")
def up(
    seed: int = typer.Option(42, "--seed", help="RNG seed passed through to the full generator."),
    reuse_dataset_path: str = typer.Option(
        None,
        "--reuse-dataset-path",
        help="If set and this path already has the expected bulk output files, skip generation.",
    ),
    now: str = typer.Option(
        str(date.today()), "--now", help="YYYY-MM-DD, anchors the generator's 3yr-back/8wk-forward window."
    ),
    target_lag: str = typer.Option(
        "1 hour",
        "--target-lag",
        help="Dynamic-table target_lag for cons__fct_priority_score (the pipeline's "
        "one leaf table; upstream tables are target_lag=DOWNSTREAM and follow this "
        "value automatically). Snowflake's real minimum is 60 seconds (SH-72).",
    ),
) -> None:
    """Spin up env + generate/load the full dataset."""
    run_up(seed, now, reuse_dataset_path, target_lag)
```

**Docstring header** (`manage.py:11-79`, the `up:` numbered-steps block): no numbered step needs a new line — this is a same-process env var, not a new pipeline stage — but the existing "6. dbt seed, then dbt run..." step's parenthetical is a reasonable place for a one-line pointer. Developer-agent may add a short note there (e.g. "target_lag for the leaf table is controlled by `--target-lag`, see SH-72") but this is cosmetic, not required for correctness — not a hard requirement of this design.

Nothing else in `manage.py` changes — confirmed via full-file read (§3.3): `run_post_setup()`, `run_down()`, `run_setup_jira()`, `run_authorize_jira_mcp()`, `inject_next_tick()`, `inject_next_batch()`, `reset_cursor()` never shell out to `dbt run` and need no env var.

---

## 5. Invariants for Reviewer-agent

1. Exactly one of the 6 model files (`cons__fct_priority_score.sql`) has a `target_lag` value that is not the literal string `DOWNSTREAM` — it must be `var('target_lag')` (or, if 3.2's fallback was needed, an inline `env_var()` call), never a hardcoded literal.
2. The other 5 model files' `target_lag` is the literal string `'DOWNSTREAM'` — not a var, not an env_var call, not left as `'1 hour'`.
3. `dbt_project.yml`'s new `target_lag` var follows the exact `env_var(..., default)` pattern already used by `oee_performance_pct`/`oee_quality_pct` — same file, same `vars:` block, same quoting style.
4. `manage.py up --target-lag <value>` (with no other flags) changes only `cons__fct_priority_score`'s materialized `target_lag` on the next `dbt run` — confirm via `dbt run --select cons__fct_priority_score` + a `SHOW DYNAMIC TABLES LIKE 'cons__fct_priority_score'` (or equivalent) live check that the `target_lag` column reflects the passed value, and that the 5 upstream tables' `target_lag` column reads `DOWNSTREAM`.
5. Running `manage.py up` with no `--target-lag` flag at all produces byte-for-byte the same `target_lag='1 hour'` behavior on `cons__fct_priority_score` as before this story shipped — the default must not silently change existing behavior.
6. No header comment anywhere in the 6 files claims a "temporarily pinned to 1 hour" constraint that no longer applies post-change (§4.2, §4.3's `std__sensor_reading.sql` and `feast__fct_sensor_features_inference.sql` edits, and the `cons__fct_priority_score.sql` cross-reference correction) — grep the 6 files for the literal substring `"temporarily pinned"` post-implementation; it should return zero matches.
7. `manage.py post-setup` and every other `manage.py` command besides `up` remain untouched — no env var, no new option — confirmed unnecessary in §3.3.
8. The first `dbt run` after this change must not error on `target_lag=var('target_lag')` inside `config()` — if it does, apply 3.2's documented fallback (inline `env_var()`), not a silent revert to a hardcoded literal.

---

## 6. Files to modify (Developer-agent's checklist)

- `predictive_maintenance_dbt/dbt_project.yml` — add `target_lag` var (§4.1)
- `predictive_maintenance_dbt/models/consumption/cons__fct_priority_score.sql` — `target_lag=var('target_lag')` + header comment rewrite (§4.2)
- `predictive_maintenance_dbt/models/standardized/std__sensor_reading.sql` — `target_lag='DOWNSTREAM'` + header comment rewrite (§4.3)
- `predictive_maintenance_dbt/models/consumption/cons__fct_sensor_reading.sql` — `target_lag='DOWNSTREAM'` only (§4.3)
- `predictive_maintenance_dbt/models/feast/feast__fct_sensor_features_inference.sql` — `target_lag='DOWNSTREAM'` + header comment rewrite (§4.3)
- `predictive_maintenance_dbt/models/consumption/cons__fct_anomaly_result.sql` — `target_lag='DOWNSTREAM'` only (§4.3)
- `predictive_maintenance_dbt/models/consumption/cons__fct_rul_prediction.sql` — `target_lag='DOWNSTREAM'` only (§4.3)
- `manage.py` — `run_up()` signature + env var line, `up` command's new `--target-lag` option (§4.4)

**Reads only** (no changes): `feast__fct_sensor_features_train.sql`, `feast__training_dataset_rul.sql`, `scripts/07_post_setup.sql`, `oee_command_center_app/pages/1_Overview.py`, `pyproject.toml`/`uv.lock`.

---

## 7. Explicitly deferred

- Any change to the semantic view or Overview page to decouple `cons__fct_sensor_reading`/`cons__fct_anomaly_result`'s dashboard-facing freshness from the leaf's `target_lag` (§3.1.2) — not asked for; the coupling is an accepted, intended consequence of a single-knob design, not a defect to fix.
- Widening `--target-lag` (or an equivalent) onto any command besides `up` (e.g. a hypothetical future `manage.py demo` sub-command that also rebuilds dynamic tables) — no such command exists today (§3.3); out of scope until one does.
- Any LLD/FRD-spec reconciliation (e.g. whether `'15 minutes'` should become the *documented* target, not just an available `--target-lag` value) — this story is purely mechanical parameterization; the demo-cadence question is a product decision for the user, not a design change bundled here.
- The `up:` docstring header's cosmetic pointer to `--target-lag` (§4.4, last paragraph) — nice-to-have, not required for correctness.
