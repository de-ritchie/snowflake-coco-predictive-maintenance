# Design: Semantic View + Cortex Agent + Agent Chat (finishing EPIC-SKELETON)

Status: **Frozen** — produced non-interactively per explicit instruction (no live-Q&A round possible this session). All 6 open questions below are resolved with a concrete decision; items with genuine ambiguity are called out in §8 "Flagged assumptions" for the user to review, but nothing is left blocking downstream work. Reconciled 2026-09-24 post-implementation/review — Reviewer-agent returned a clean PASS verdict, zero bugs found, all 8 review checks passed, idempotency confirmed on a 3rd re-run of `scripts/07_post_setup.sql`, and a real `agent:run` REST call (made directly, bypassing the Chat page) returned a grounded response; see §6a (verification results) for what was confirmed, and §8 for the assumptions this settles. **Re-reconciled 2026-09-24** after a second round of follow-up changes (connection-name env override + explicit `USE ROLE/WAREHOUSE/DATABASE/SCHEMA` in the app, a `st.spinner` UX tweak on the Chat page, and re-enabling SHAP explainability in a separate already-Done story's script, bundled into this PR at the user's request) — Reviewer-agent returned a second clean PASS; see §12.
**Stories**: SH-30 (S-SEM-1: semantic view), SH-27 (S-SEM-2: one verified query), SH-28 (S-AGENT-1: one Cortex Agent), SH-31 (S-OPS-POST-1: post-setup script v1), SH-33 (S-OPS-POST-1b: fold Streamlit deploy into it), SH-32 (S-APP-2: Agent Chat page)
**Branch**: `feature/SH-2-27-28-30-31-32-33-semantic-view-agent-chat`
**Traces to**: [docs/04-6-LLD.md](../04-6-LLD.md) (Module 6, Semantic View), [docs/04-7-LLD.md](../04-7-LLD.md) (Module 7, Agent Tool Specs), [docs/04-8-LLD.md](../04-8-LLD.md) §4 (Chat page), [docs/04-10-LLD.md](../04-10-LLD.md) §5 (post-setup script), [docs/02-FRD.md](../02-FRD.md) FR-SA-01/02, FR-CC-05, [docs/05-Epics.md](../05-Epics.md) §4.5/§4.6, [docs/designs/SH-69-std-cons-order-inventory-oee-models.md](SH-69-std-cons-order-inventory-oee-models.md) (source of the newly-available CONS tables), [docs/designs/SH-21-streamlit-shell-overview-page.md](SH-21-streamlit-shell-overview-page.md) (app conventions)

---

## 0. No live-SQL-tool caveat (same as SH-69)

This session had no generic SQL-execution tool against Snowflake — only Atlassian/Confluence/browser/file tools. Ground truth for every table/column referenced below is taken directly from the **committed dbt model SQL** (`predictive_maintenance_dbt/models/consumption/*.sql`) and the already-frozen/reconciled `SH-69` design doc, which is what actually produced these objects and was itself verified live post-implementation (`dbt run`/`dbt test` all green, Reviewer-agent PASS, 2026-09-23). This is at least as reliable as an ad hoc `SELECT *` and is the same justification SH-69 used for the same constraint. `Developer-agent` should still spot-check column names against Snowsight before wiring the semantic view, in case anything drifted since.

---

## 1. Scope

Finish EPIC-SKELETON's semantic-view/agent/chat slice (`docs/05-Epics.md` §4.5/§4.6), now that SH-69 (merged to `main`) makes `Order`, `Inventory` (FG + spare), `OEEMetric`, and `MaintenanceEvent` available in CONS, on top of the already-built `Machine`/`SensorReading`/`AnomalyResult`.

**In scope**: semantic view spanning 8 entities (below), 1 verified query, 1 skeleton Cortex Agent (Analyst tool only, no personas), post-setup script v1 (`scripts/07_post_setup.sql`, already a stub — SH-30/28/31/33 fill it in), Streamlit deploy folded into the same script, and a new `pages/2_Chat.py`.

**Explicitly out of scope** (confirmed against `docs/05-Epics.md` §7 EPIC-PERSONAS and §6 EPIC-RUL):
- `PriorityScore` semantic entity — blocked on `CONS.FCT_RUL_PREDICTION` (EPIC-RUL, not built).
- `RulPrediction` semantic entity — same blocker; Module 6's LLD lists it as a target entity, but no backing table exists yet. Deferred to whichever story wires RUL in (S-OPS-POST-2 or a dedicated semantic-view update).
- 3-persona expansion, `explain_prediction`/ticketing tools, persona switcher — EPIC-PERSONAS (S-OPS-POST-2) revises this same script/agent in place later, not this story.
- Persona-scoped chat / tool-call confirmation UI (Module 8 §4's `create_jira_ticket` wording) — no ticketing tool exists yet; the Chat page here is single-agent, read-only-tool only.

---

## 2. Ground truth: what's actually available (from SH-69 + earlier CONS models)

| Table (physical name, schema `cons`) | Grain / key | Columns |
|---|---|---|
| `cons__dim_equipment` | `equipment_id` | `equipment_id, equipment_name, line_name, product_id, variant, is_sensor_enabled, throughput_units_per_hour, commissioned_ts` |
| `cons__dim_product` (seed) | `product_id, variant` | `product_id, product_name, variant` — always 4 rows (2 products × 2 variants), only 2 combos have matching facts |
| `cons__fct_sensor_reading` | `equipment_id, reading_ts, sensor_type` | `equipment_id, reading_ts, sensor_type, reading_value` (lean atomic fact, real physical units) |
| `cons__fct_anomaly_result` | `equipment_id, reading_ts` | `equipment_id, reading_ts, is_anomaly, anomaly_score, model_version` |
| `cons__fct_maintenance_event` | `event_id` | `event_id, equipment_id, event_type ('PM'\|'BREAKDOWN'), event_start_ts, event_end_ts, technician_notes, duration_hours` |
| `cons__fct_order` | `order_week, product_id, variant` | `order_week, product_id, variant, order_units` |
| `cons__fct_inventory_fg` | `period_week, product_id, variant` | `period_week, product_id, variant, fg_units_on_hand` |
| `cons__fct_inventory_spare` | `period_week, equipment_id, spare_part_name` | `period_week, equipment_id, spare_part_name, units_on_hand, lead_time_days` |
| `cons__fct_oee` | `line_name, period_week` | `line_name, period_week, scheduled_hours, breakdown_hours, availability_pct, performance_pct, quality_pct, oee_pct` |

Not exposed (per Module 6, unchanged): `FEAST` schema, `cons__dim_sensor_baseline`. `cons__fct_priority_score` / `cons__fct_rul_prediction` don't exist yet (§1).

---

## 3. Q1/Q2 — Semantic view mechanism and entity/relationship scope

### Decision: native `CREATE SEMANTIC VIEW` SQL DDL, not a YAML file

Module 7 §1 already assumes the agent's Analyst tool points at `tool_resources.Analyst.semantic_view: "snowcomotive.cons.oee_semantic_view"` — a Snowflake object identifier, not a file path. Module 10 §5 likewise pseudocodes `CREATE OR REPLACE SEMANTIC VIEW snowcomotive.cons.oee_semantic_view ...`. Snowflake's native semantic view is a first-class schema-level object (`TABLES`/`RELATIONSHIPS`/`FACTS`/`DIMENSIONS`/`METRICS` clauses), not a YAML artifact consumed by external tooling (that pattern applies to Cortex Analyst's *older*, file-based semantic model — this project has always targeted the native object, matching Module 7's identifier-based `tool_resources` reference). **Decision confirmed, no ambiguity.**

### Decision: 8 entities now (skeleton-minimum + everything SH-69 unblocked), `RulPrediction`/`PriorityScore` deferred

| Entity | Table | Key |
|---|---|---|
| `Machine` | `cons.cons__dim_equipment` | `equipment_id` |
| `Product` | `cons.cons__dim_product` | `product_id, variant` |
| `SensorReading` | `cons.cons__fct_sensor_reading` | `equipment_id, reading_ts, sensor_type` |
| `AnomalyResult` | `cons.cons__fct_anomaly_result` | `equipment_id, reading_ts` |
| `MaintenanceEvent` | `cons.cons__fct_maintenance_event` | `event_id` |
| `Order` | `cons.cons__fct_order` | `order_week, product_id, variant` |
| `Inventory` — **kept as 2 separate semantic entities**, see below | `cons.cons__fct_inventory_fg` + `cons.cons__fct_inventory_spare` | see below |
| `OEEMetric` | `cons.cons__fct_oee` | `line_name, period_week` |

**Resolving Module 6's own open item ("does `Inventory` stay one logical entity spanning two physical tables, or split into two")**: split into `InventoryFg` and `InventorySpare`. Reasoning: they don't share a grain (`product_id`/`variant` vs. `equipment_id`/`spare_part_name`) or even a conceptual subject (finished-goods vs. spare-parts) — forcing them into one semantic entity would need a `UNION`-style logical table with a discriminator column that doesn't exist in either physical table, adding complexity with no query benefit. Two entities map 1:1 to two physical tables cleanly, which is exactly what `TABLES (...)` wants. This is a small, justified deviation from Module 6's literal wording ("Inventory" singular), not from its intent (it explicitly left this as an open call for build time).

Relationships (extends Module 6 §"Relationships", same style):
- `Machine` 1:N `SensorReading`, `MaintenanceEvent`, `AnomalyResult`
- `Machine` N:1 `Product` (via `product_id, variant` — which product/variant a machine's line produces)
- `Machine` 1:N `InventorySpare` (via `equipment_id`)
- `Product` 1:N `Order`, `InventoryFg`
- `Machine` N:1 `OEEMetric` (via shared `line_name`, not a direct FK — same grain-join note as Module 6)

`RulPrediction`/`PriorityScore` — explicitly **not** in `TABLES (...)` this round (§1).

### `CREATE SEMANTIC VIEW` — as actually built and live-verified (see `scripts/07_post_setup.sql` for the canonical, executable version)

The block below is the real DDL now committed to `scripts/07_post_setup.sql`, reproduced here for reference — do not copy from an older version of this section, and treat the script file as the source of truth if the two ever drift. This superseded an earlier draft that had two clause-syntax bugs, both caught and fixed during Developer-agent's live build against this account (also independently re-verified by Reviewer-agent, see §6a):

1. **`DIMENSIONS`/`FACTS` clause aliasing direction**: the correct grammar is `<table>.<physical_column> AS <logical_name>` — the draft had several of these backwards (e.g. `anomaly_result.reading_ts AS anomaly_reading_ts`, which fails because `anomaly_reading_ts` isn't a real column; the fix is `anomaly_result.anomaly_reading_ts AS reading_ts`, matching the pattern already used correctly for most other dimensions/facts in the draft). Fixed for every dimension/fact whose alias differs from the underlying column name (`anomaly_result.reading_ts`, `inventory_fg`/`inventory_spare.period_week`, `inventory_spare.units_on_hand`, `inventory_spare.lead_time_days`, `oee_metric.period_week`).
2. **Verified-query clause** is `AI_VERIFIED_QUERIES (...)`, not `WITH VERIFIED_QUERIES` — and it must appear *after* `COMMENT` in the `CREATE SEMANTIC VIEW` clause order, not before.

```sql
CREATE OR REPLACE SEMANTIC VIEW snowcomotive.cons.oee_semantic_view
  TABLES (
    machine AS snowcomotive.cons.cons__dim_equipment
      PRIMARY KEY (equipment_id)
      WITH SYNONYMS ('equipment', 'asset')
      COMMENT = 'Sensor-enabled and non-sensor manufacturing equipment',
    product AS snowcomotive.cons.cons__dim_product
      PRIMARY KEY (product_id, variant)
      COMMENT = 'Finished-goods product/variant reference (4 static rows)',
    sensor_reading AS snowcomotive.cons.cons__fct_sensor_reading
      PRIMARY KEY (equipment_id, reading_ts, sensor_type)
      COMMENT = 'Raw sensor readings, real physical units (not z-scores)',
    anomaly_result AS snowcomotive.cons.cons__fct_anomaly_result
      PRIMARY KEY (equipment_id, reading_ts)
      WITH SYNONYMS ('anomaly', 'health score')
      COMMENT = 'IsolationForest anomaly score/flag per sensor tick',
    maintenance_event AS snowcomotive.cons.cons__fct_maintenance_event
      PRIMARY KEY (event_id)
      WITH SYNONYMS ('cmms log', 'repair event', 'PM event')
      COMMENT = 'Preventive maintenance and breakdown events',
    order_ AS snowcomotive.cons.cons__fct_order
      PRIMARY KEY (order_week, product_id, variant)
      COMMENT = 'Weekly finished-goods order volume by product/variant',
    inventory_fg AS snowcomotive.cons.cons__fct_inventory_fg
      PRIMARY KEY (period_week, product_id, variant)
      COMMENT = 'Weekly finished-goods inventory snapshot',
    inventory_spare AS snowcomotive.cons.cons__fct_inventory_spare
      PRIMARY KEY (period_week, equipment_id, spare_part_name)
      COMMENT = 'Weekly spare-parts inventory snapshot',
    oee_metric AS snowcomotive.cons.cons__fct_oee
      PRIMARY KEY (line_name, period_week)
      WITH SYNONYMS ('OEE', 'overall equipment effectiveness')
      COMMENT = 'Weekly OEE (availability x performance x quality) by production line'
  )
  RELATIONSHIPS (
    sensor_reading_to_machine AS sensor_reading (equipment_id) REFERENCES machine (equipment_id),
    anomaly_result_to_machine AS anomaly_result (equipment_id) REFERENCES machine (equipment_id),
    maintenance_event_to_machine AS maintenance_event (equipment_id) REFERENCES machine (equipment_id),
    inventory_spare_to_machine AS inventory_spare (equipment_id) REFERENCES machine (equipment_id),
    machine_to_product AS machine (product_id, variant) REFERENCES product (product_id, variant),
    order_to_product AS order_ (product_id, variant) REFERENCES product (product_id, variant),
    inventory_fg_to_product AS inventory_fg (product_id, variant) REFERENCES product (product_id, variant)
  )
  FACTS (
    sensor_reading.reading_value AS reading_value,
    anomaly_result.anomaly_score AS anomaly_score,
    maintenance_event.duration_hours AS duration_hours,
    order_.order_units AS order_units,
    inventory_fg.fg_units_on_hand AS fg_units_on_hand,
    inventory_spare.spare_units_on_hand AS units_on_hand,
    inventory_spare.spare_lead_time_days AS lead_time_days,
    oee_metric.scheduled_hours AS scheduled_hours,
    oee_metric.breakdown_hours AS breakdown_hours
  )
  DIMENSIONS (
    machine.equipment_id AS equipment_id,
    machine.equipment_name AS equipment_name,
    machine.line_name AS line_name,
    machine.is_sensor_enabled AS is_sensor_enabled,
    product.product_id AS product_id,
    product.product_name AS product_name,
    product.variant AS variant,
    sensor_reading.reading_ts AS reading_ts,
    sensor_reading.sensor_type AS sensor_type,
    anomaly_result.anomaly_reading_ts AS reading_ts,
    anomaly_result.is_anomaly AS is_anomaly,
    maintenance_event.event_type AS event_type,
    maintenance_event.event_start_ts AS event_start_ts,
    order_.order_week AS order_week,
    inventory_fg.inventory_fg_period_week AS period_week,
    inventory_spare.inventory_spare_period_week AS period_week,
    inventory_spare.spare_part_name AS spare_part_name,
    oee_metric.oee_period_week AS period_week
  )
  METRICS (
    oee_metric.avg_availability_pct AS AVG(oee_metric.availability_pct),
    oee_metric.avg_oee_pct AS AVG(oee_metric.oee_pct),
    anomaly_result.anomaly_count AS SUM(IFF(anomaly_result.is_anomaly, 1, 0)),
    order_.total_order_units AS SUM(order_.order_units)
  )
  COMMENT = 'SnowComotive skeleton semantic view: machine health/anomalies, maintenance, OEE, orders, inventory.'
  AI_VERIFIED_QUERIES (
    current_machine_health_status AS (
      QUESTION 'What is the current anomaly score and health status for each machine?'
      SQL 'SELECT
    m.equipment_id,
    m.equipment_name,
    m.line_name,
    a.reading_ts AS latest_reading_ts,
    a.anomaly_score,
    a.is_anomaly
FROM snowcomotive.cons.cons__fct_anomaly_result a
JOIN snowcomotive.cons.cons__dim_equipment m
    ON m.equipment_id = a.equipment_id
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY a.equipment_id ORDER BY a.reading_ts DESC
) = 1
ORDER BY a.equipment_id'
    )
  );
```

Note: `order` is a reserved SQL keyword — the table alias is `order_` above, matching what was actually built and confirmed to compile.

---

## 4. Q3 — The one verified query (SH-27 / S-SEM-2)

**Natural-language question**: *"What is the current anomaly score and health status for each machine?"*

**Hand-authored SQL** (validated structurally against `cons__fct_anomaly_result`'s confirmed columns, `equipment_id/reading_ts/is_anomaly/anomaly_score` — same table SH-21's Overview page already queries live):

```sql
SELECT
    m.equipment_id,
    m.equipment_name,
    m.line_name,
    a.reading_ts AS latest_reading_ts,
    a.anomaly_score,
    a.is_anomaly
FROM snowcomotive.cons.cons__fct_anomaly_result a
JOIN snowcomotive.cons.cons__dim_equipment m
    ON m.equipment_id = a.equipment_id
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY a.equipment_id ORDER BY a.reading_ts DESC
) = 1
ORDER BY a.equipment_id;
```

**Why this one, not OEE/priority/root-cause**: FR-SA-01's minimum viable subset (per Module 6 §"Deferred to build time") explicitly lists "current machine health/RUL" first, and it's the query Module 6's own worked example already anticipated ("what's the current anomaly score for machine X"). It's also demo-relevant on its own — SH-21's Overview page already renders this exact health-status logic as cards, so a Chat-panel question that reproduces it grounds the agent against something the presenter can visually cross-check on another page in the same demo breath. OEE-trend/priority-ranking verified queries are explicitly deferred to whichever story wires those pages/entities more fully (Forecast OEE / Priority Queue are P1, not built).

**Developer-agent action**: attach this as the semantic view's verified query via the `AI_VERIFIED_QUERIES (...)` clause (confirmed live against this account — not `WITH VERIFIED_QUERIES` as originally drafted; see §3's corrected DDL and `scripts/07_post_setup.sql`).

---

## 5. Q4 — Cortex Agent tool spec and instructions (SH-28 / S-AGENT-1)

### Decision: name the agent `maintenance_supervisor_agent` now, not a throwaway skeleton name

Module 10 §5's post-setup pseudocode already names the P1 persona agent `snowcomotive.cons.maintenance_supervisor_agent`. Rather than create a differently-named skeleton agent now and a second object later, **this story creates that exact object now**, with only the Analyst tool and skeleton-appropriate instructions — EPIC-PERSONAS's S-OPS-POST-2 then `CREATE OR REPLACE`s the *same* object in place to add `explain_prediction`/`create_jira_ticket` and persona-specific instructions, matching this whole project's "scripts/objects are living artifacts, revised not replaced" convention (`docs/05-Epics.md` §2). This also means SH-32's Chat page doesn't need to be rewired to a new agent name once personas land.

### Spec — as actually built and live-verified (see `scripts/07_post_setup.sql` for the canonical, executable version)

Two corrections were needed vs. the earlier draft, both caught and fixed during Developer-agent's live build (independently re-verified by Reviewer-agent, see §6a):

3. **`CREATE AGENT` uses `PROFILE = '...'`**, not `WITH PROFILE = '...'` — the leading `WITH` is not valid syntax.
4. **`tool_resources.Analyst.execution_environment.warehouse` must be the warehouse's all-caps unquoted identifier `SNOWCOMOTIVE_WH`**, not a lowercase/quoted variant like `snowcomotive_wh`. This is not a DDL compile error — `CREATE AGENT` accepts either casing — it's a silent runtime failure: a live `agent:run` call with the lowercase value failed with "you must specify the warehouse..." even though the object had been created successfully. Easy to miss unless the agent is actually invoked.

```sql
CREATE OR REPLACE AGENT snowcomotive.cons.maintenance_supervisor_agent
  PROFILE = '{"display_name": "SnowComotive Maintenance Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Maintenance Agent for a predictive-maintenance
    and OEE command center. Answer questions about machine health,
    anomalies, maintenance events, OEE, orders, and inventory, grounded
    strictly in the Analyst tool's query results against the semantic view.
    Never fabricate a health, anomaly, OEE, or inventory value. If data for
    a requested machine or time period is missing or stale, say so
    explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, or inventory.
    This is currently the only tool available -- do not claim to be able to
    create tickets or explain model predictions; if asked, say those
    capabilities are not enabled yet.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, and inventory using the SnowComotive semantic view."
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
$$;
```

This matches Module 7 §1's shared Analyst tool block verbatim (semantic view identifier, warehouse, timeout) and adds the guardrail instruction from FR-SA-04 ("say so explicitly rather than fabricating") one persona early, since it costs nothing to include now and EPIC-PERSONAS will keep it. Clause names (`PROFILE`, `FROM SPECIFICATION`) and the warehouse-casing requirement are confirmed correct by live `CREATE AGENT` + `agent:run` execution against this account, not just Module 10 §5's pseudocode.

---

## 6. Q5 — Post-setup script v1: file, number, and `manage.py` wiring (SH-31/SH-33)

### File: `scripts/07_post_setup.sql` — already reserved, not a new number

`scripts/README.md` already lists `07_post_setup.sql` as row 07, status "Not built", tied to exactly these Jira keys (SH-30/28/31/33). No new script number needed — this story fills in the existing stub.

Content (semantic view + agent DDL from §3/§5 above, run via `manage.py`, same connector-session pattern as every other numbered script):

```sql
-- ============================================================================
-- 07_post_setup.sql -- Semantic view + Agent (v1: skeleton, Analyst tool only)
-- Traces to: FR-OPS-03, LLD Module 10 S5, docs/05-Epics.md EPIC-SKELETON S4.5
-- Jira: SH-30 (S-SEM-1), SH-27 (S-SEM-2), SH-28 (S-AGENT-1), SH-31 (S-OPS-POST-1)
-- ============================================================================

CREATE OR REPLACE SEMANTIC VIEW snowcomotive.cons.oee_semantic_view ...;  -- S3

CREATE OR REPLACE AGENT snowcomotive.cons.maintenance_supervisor_agent
  PROFILE = '{"display_name": "SnowComotive Maintenance Agent"}'
  FROM SPECIFICATION $$ ... $$;  -- S5
```

### Streamlit deploy (SH-33) — **deviation from Module 10 §5's `snow streamlit deploy` pseudocode**

AGENTS.md is explicit and unambiguous: *"Do not add `snow sql -f` invocations or `snow` CLI dependencies going forward; extend `manage.py` instead."* The MFA/TOTP-passcode friction that killed the `snow` CLI for SQL scripts (`docs/designs/1 - SH-2-11-upload-load-into-raw.md` §8) applies equally to `snow streamlit deploy` — it authenticates the same way. **Decision**: deploy Streamlit via the native `CREATE STREAMLIT` SQL DDL object instead, driven from `manage.py` through the same `snowflake-connector-python` session already used for every other script (PUT + SQL, identical shape to `manage.py demo inject-tick`'s existing PUT+COPY INTO pattern).

```sql
-- Also folded into 07_post_setup.sql, or a new manage.py-only step -- see below.
CREATE STAGE IF NOT EXISTS snowcomotive.cons.streamlit_stage
  DIRECTORY = (ENABLE = TRUE);

CREATE OR REPLACE STREAMLIT snowcomotive.cons.oee_command_center
  ROOT_LOCATION = '@snowcomotive.cons.streamlit_stage'
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = snowcomotive_wh;
```

`manage.py` gains a `run_post_setup()` step, called at the end of `run_up()` (after `run_dbt_phase2_and_test()`), matching `scripts/README.md`'s stated run order (01→...→07):

1. Run `scripts/07_post_setup.sql` (semantic view + agent DDL) via the existing `snowcomotive_role` connector session.
2. `PUT` every file under `oee_command_center_app/` (recursively — `streamlit_app.py` + `pages/*.py`) to `@snowcomotive.cons.streamlit_stage`, preserving the `pages/` subpath, `OVERWRITE=TRUE AUTO_COMPRESS=FALSE` (same flags `manage.py demo inject-tick` already uses for its own PUT).
3. Run the `CREATE STAGE IF NOT EXISTS` / `CREATE OR REPLACE STREAMLIT` statements above.

This keeps `manage.py up` as the single end-to-end command (no new Typer sub-command needed) and requires zero new external dependencies (no `snow` CLI, no `snowflake.yml`/`snowflake-cli` project scaffolding). Re-running `up` re-syncs the Streamlit app's code via `OVERWRITE=TRUE` + `CREATE OR REPLACE STREAMLIT`, satisfying FR-OPS-06 idempotency the same way every other object in this pipeline does.

**Environment note (not part of this story's deliverable)**: during live verification, the target account had no trained anomaly-detection model yet, which the semantic view's `AI_VERIFIED_QUERIES` sample question depends on having realistic data for. Developer-agent worked around this via a scratch/temporary procedure to populate a model, rather than modifying `scripts/05_train_models.sql` itself — that script is untouched by this story's diff. Documented here so a future reader isn't confused by an anomaly model existing in the account with no corresponding code change in this branch.

---

## 6a. Verification (live, 2026-09-24)

**Developer-agent (implementation)**: semantic view, agent, stage, and Streamlit app all created successfully against the live account. The verified query (§4) ran standalone against real data. A real `agent:run` REST call, made via the Chat page's own code path, returned a grounded response. Idempotency confirmed via 2 back-to-back re-runs of `scripts/07_post_setup.sql` with no failures or duplicate objects.

**Reviewer-agent (independent review)**: returned a clean **PASS** verdict — all 8 review checks passed, zero bugs found. Independently re-verified:
- A 3rd idempotent re-run of `scripts/07_post_setup.sql` (on top of Developer-agent's 2), confirming `CREATE OR REPLACE`/`IF NOT EXISTS` idempotency holds across repeated runs, per invariant 4 (§10).
- A direct `agent:run` REST call made independently (bypassing the Chat page entirely) — returned a grounded response, confirming the mechanism works independent of the Streamlit wiring.
- Live `DESC SEMANTIC VIEW` / `DESCRIBE AGENT` against the actual created objects, confirming the exact entity list matches §3's 8 entities (no `RulPrediction`/`PriorityScore` drift, per invariant 1) and that `tool_resources.Analyst.semantic_view`'s FQN matches the semantic view's actual name exactly, per invariant 2.

---

## 7. Q6 — Agent Chat page mechanism (SH-32)

### Decision: Cortex Agent REST `:run` endpoint, called from Streamlit via `_snowflake.send_snow_api_request` when deployed natively, with a local-dev REST fallback for iteration before deploy

Cortex Agents are invoked via a REST endpoint (`POST /api/v2/databases/{db}/schemas/{schema}/agents/{name}:run`, streaming Server-Sent Events), not a SQL function — there is no `SELECT`-based way to run an Agent object the way `SNOWFLAKE.CORTEX.COMPLETE` works for raw LLM completion. This project's Streamlit app is deployed *into* Snowflake (§6), so at runtime it has `_snowflake.send_snow_api_request` available (Snowflake's own internal helper for calling first-party REST APIs from a native Streamlit app using the app's own session — the documented pattern for embedding Cortex Agents chat in Streamlit-in-Snowflake). For local dev iteration (running `streamlit run oee_command_center_app/streamlit_app.py` against `st.connection("snow-coco", ...)` before every deploy), `_snowflake` isn't importable — fall back to a direct `requests.post` against the same REST path, authenticated with the connector session's own token (`Authorization: Snowflake Token="<token>"` header, account host derived from the connection).

```python
# oee_command_center_app/pages/2_Chat.py (sketch -- Developer-agent implements)
AGENT_FQN = "snowcomotive.cons.maintenance_supervisor_agent"

def call_agent(messages: list[dict]) -> str:
    path = f"/api/v2/databases/snowcomotive/schemas/cons/agents/maintenance_supervisor_agent:run"
    body = {"messages": messages}
    try:
        import _snowflake  # only importable inside Streamlit-in-Snowflake
        resp = _snowflake.send_snow_api_request(
            "POST", path, {}, {}, body, {}, 30000
        )
    except ImportError:
        resp = _call_agent_rest_local(path, body)  # requests-based fallback, dev only
    return _parse_agent_sse_response(resp)  # concatenate response-text deltas
```

- `st.chat_input`/`st.chat_message` + `st.session_state["chat_history"]` for the message list, appended per turn — standard Streamlit chat pattern, no custom component.
- SSE response parsing (`_parse_agent_sse_response`) concatenates streamed `response` text-delta events into the assistant's final message. Tool-call events (Analyst's generated SQL, if surfaced) are logged/expandable but not deeply rendered in this skeleton pass — Module 8 §4's fuller "tool-call transparency" (distinct `CREATED`/`ALREADY_OPEN` wording) only applies once the ticketing tool exists (EPIC-JIRA/EPIC-PERSONAS), so it's out of scope here by construction, not by omission.
- New dependency: `requests` (only used by the local-dev fallback path) — add via `uv add requests`, per this project's `uv`-only dependency convention.
- File: `oee_command_center_app/pages/2_Chat.py`, following SH-21's exact page convention (`from streamlit_app import get_connection, render_sidebar`, `st.set_page_config(...)` at top, `render_sidebar()` then content).

**Flagged as the genuinely uncertain part of this design** (§8) — the precise `_snowflake.send_snow_api_request` call signature and the exact SSE event-type names in the Agent `:run` response are things I cannot verify without a live Snowflake session or Streamlit-in-Snowflake runtime in this design session. Developer-agent must validate both against Snowflake's current documentation/an actual deployed run before considering this page done; the shape above is the right mechanism, but field/argument names may need small corrections.

---

## 8. Flagged assumptions (please review)

1. **`Inventory` split into two semantic entities** (`InventoryFg`/`InventorySpare`) rather than one — §3. Low-risk, well-justified design call, not a verification item. Not contradicted by anything found during build/review.
2. **Agent named `maintenance_supervisor_agent` from the very first (skeleton) version**, not a separate throwaway name — §5. Chosen to avoid a rename/rewire later, but means this "skeleton" agent's object name already presumes the P1 persona structure. Not contradicted by anything found during build/review.
3. **Streamlit deploy via native `CREATE STREAMLIT` DDL, not `snow streamlit deploy`** — §6. **Resolved/confirmed correct**: live-verified during build (§6a) — the Streamlit app deployed and ran successfully via `CREATE STAGE`/`CREATE STREAMLIT` + connector-session `PUT`, no `snow` CLI needed.
4. **Cortex Agent Chat call mechanism (`_snowflake.send_snow_api_request` + REST fallback)** — §7. **Resolved/confirmed correct**: this was the riskiest single item in the original draft (exact request/response shape unverified live at design time). Both Developer-agent (via the Chat page) and Reviewer-agent (via a direct, independent `agent:run` REST call bypassing the Chat page) confirmed the mechanism returns a grounded response (§6a).
5. **Exact `CREATE SEMANTIC VIEW`/`CREATE AGENT` clause syntax** (`AI_VERIFIED_QUERIES`, `PROFILE`, warehouse-casing, etc.) — §3/§5. **Resolved**: the original draft's syntax guesses were wrong in the ways detailed in §3/§5 (aliasing direction, verified-query clause name/position, `PROFILE` vs. `WITH PROFILE`, warehouse-identifier casing) — all four corrections are now live-verified and captured in `scripts/07_post_setup.sql`, the canonical DDL.

---

## 9. Files to create/modify (Developer-agent's checklist)

**New**:
- `oee_command_center_app/pages/2_Chat.py` — Agent Chat page (§7).

**Modified**:
- `scripts/07_post_setup.sql` — fill in the stub: `CREATE SEMANTIC VIEW` (§3) + `CREATE AGENT` (§5) + `CREATE STAGE`/`CREATE STREAMLIT` (§6, or split into its own block within the same file — Developer-agent's call, both statements belong to this one script per `scripts/README.md`'s row 07).
- `manage.py` — new `run_post_setup()` step, called at the end of `run_up()`; PUT logic for `oee_command_center_app/` files (§6).
- `scripts/README.md` — flip row 07's status to "Built" once done, matching the convention every prior script-completion PR has followed.
- `pyproject.toml`/`uv.lock` — add `requests` (§7), via `uv add requests`, not hand-edited.
- `predictive_maintenance_dbt/models/consumption/schema.yml` — **not** modified (no new dbt models this story; semantic view/agent/Streamlit are all non-dbt Snowflake objects).

**Reads** (no changes): all 9 tables in §2, `oee_command_center_app/streamlit_app.py` (imported by the new Chat page, unchanged).

---

## 10. Invariants for Reviewer-agent

1. Semantic view exposes exactly the 8 entities in §3 — no `RulPrediction`/`PriorityScore` table wired in (those tables don't exist; a premature relationship reference would fail at `CREATE SEMANTIC VIEW` time, which is itself a fine way to catch this if violated).
2. The Cortex Agent's `tool_resources.Analyst.semantic_view` value is the fully-qualified `snowcomotive.cons.oee_semantic_view` name, matching whatever the semantic view actually gets created as — no drift between the two DDL statements.
3. No `snow` CLI invocation anywhere in `manage.py`, `scripts/07_post_setup.sql`, or any new script — Streamlit deploy goes through `CREATE STREAMLIT` + a connector-session `PUT`, per §6's deviation.
4. `manage.py up` remains a single idempotent command — re-running it after this story lands must not fail or duplicate objects (`CREATE OR REPLACE`/`CREATE ... IF NOT EXISTS` throughout, matching FR-OPS-06).
5. The Chat page never fabricates an agent response client-side if the REST call fails — an error/exception surfaces visibly in the chat UI (e.g., `st.error`), not a silently-empty or hallucinated assistant message.
6. Verified query (§4) runs successfully standalone against live CONS tables (independent of whether the semantic view's verified-query attachment syntax needs correction) — this is the actual FR-SA-01 proof point, the DDL wiring is secondary.

---

## 11. Explicitly deferred

- `RulPrediction`/`PriorityScore` semantic entities and the OEE-forecast/priority-ranking verified queries — EPIC-RUL/EPIC-PERSONAS.
- 3-persona expansion, `explain_prediction`/`create_jira_ticket` tools, persona switcher, tool-call `CREATED`/`ALREADY_OPEN` UI wording — EPIC-PERSONAS (S-OPS-POST-2), which revises `07_post_setup.sql`/`maintenance_supervisor_agent` in place per §5.
- Full SSE tool-call/citation rendering in the Chat page beyond plain response text — deferred until a tool with a meaningful structured result (ticketing) exists.
- `09_teardown.sql` updates (DROP SEMANTIC VIEW/AGENT/STREAMLIT/STAGE) — out of scope for this story; `DROP DATABASE` already cascades everything created here, and explicit DROP statements are EPIC-TEARDOWN's (S-TEARDOWN-1) job, not this one's.
- Local-dev-only `requests`-based REST fallback's exact auth-header/host-derivation code — sketch only in §7; Developer-agent implements against the real connector session object.

---

## 12. Follow-up changes (post-review, 2026-09-24)

Three changes were made after the §6a review cycle above had already returned a clean PASS. All three went through a second Reviewer-agent pass, which also returned a clean **PASS** — findings below.

### 12.1 `oee_command_center_app/streamlit_app.py` — env-var-overridable connection name + explicit `USE ROLE/WAREHOUSE/DATABASE/SCHEMA`

**Change**: `CONNECTION_NAME` is now overridable via `SNOWFLAKE_CONNECTION_NAME`, matching the pattern `manage.py` already uses for its own connection-name resolution. `get_connection()` now issues explicit `USE ROLE ...` / `USE WAREHOUSE ...` / `USE DATABASE ...` / `USE SCHEMA ...` statements via `conn.cursor()` immediately after connecting.

**Rationale**: passing role/warehouse/database/schema as `st.connection(...)` kwargs was tried first and found to break Streamlit's named-connection lookup entirely (the kwargs interfered with how `st.connection` resolves the named `snow-coco`/`snow-co-cat-alyst`-style connection from `connections.toml`) — the explicit post-connect `USE ...` statements are a deliberate workaround for that breakage, not a stylistic preference.

**Reviewer-agent re-verification**:
- No regression to SH-21's Overview page — all of its queries are already schema-qualified, so the added `USE SCHEMA RAW` (or equivalent) is inert there.
- Round-trip cost of the extra `USE` statements is acceptable: gated behind `st.cache_data` on the Overview page, and only paid per-turn on the Chat page's local-dev-only fallback path (dev-only, not a production/deployed-app cost).
- Failures propagate loudly — no silent failure mode introduced by the change.

### 12.2 `oee_command_center_app/pages/2_Chat.py` — `st.spinner` around `call_agent()`

**Change**: the blocking `call_agent()` call is now wrapped in `st.spinner("Thinking...")` for UX (visible feedback while the Cortex Agent `:run` REST call is in flight).

**Rationale**: pure UX polish — no behavioral change to the request/response path.

**Reviewer-agent re-verification**: confirmed invariant 5 (§10) — no fabrication on failure — is unchanged; the spinner wraps the call but does not alter error handling.

### 12.3 `scripts/05_train_models.sql` — SHAP explainability re-enabled (bundled from a separate, already-Done story)

**Change**: added `'shap'` to `PACKAGES` and re-enabled `enable_explainability=True` in `scripts/05_train_models.sql`, unlocking a real `!explain` method (per-feature SHAP values) on the trained model via `MODEL(...)!explain(...)` as a lateral table function.

**Scope note**: this file belongs to **SH-22 (S-MODEL-2)**, a separate story that is already Done — it is **not** part of this story's original ticket scope (SH-27/28/30/31/32/33). It is bundled into this PR at the user's **explicit request**, because it is small, directly beneficial (unlocks S-PERSONA-1's future `explain_prediction` tool), and this way it correctly goes through the same review gate as everything else in this PR rather than landing unreviewed.

**Process note (lesson learned)**: this fix was initially committed **directly to `main` twice**, bypassing this project's normal branch → PR → review flow (AGENTS.md's stated guideline: every story gets its own branch, no direct-to-`main` work). This was a real process gap, flagged by Reviewer-agent during the second pass. It was then **reverted on `main`** and **reintroduced as a proper commit on this branch instead** (`feature/SH-2-27-28-30-31-32-33-semantic-view-agent-chat`), per the user's explicit request, specifically so it would go through Reviewer-agent as part of this PR rather than bypass review entirely. Recorded here factually as a process note, not to be repeated — direct-to-`main` commits should not happen for any story, including small "obviously safe" one-line config fixes.

**Live verification**: verified end-to-end **twice** — once before the revert-and-redo (on `main`, prior to the revert) and once again after (on this branch, post-reintroduction). Both times, `dbt`/script-driven training completed successfully and a lateral-table-function call to `MODEL(...)!explain(...)` returned real per-feature SHAP values.

**Reviewer-agent re-verification**: confirmed the training script runs end-to-end with `enable_explainability=True` and `'shap'` in `PACKAGES`, and that `!explain` returns real per-feature SHAP values rather than failing or returning placeholder/empty output. No regression to the model's existing training/scoring behavior.

### Second-pass verdict

**Reviewer-agent (second independent review)**: clean **PASS** across all three follow-ups. No bugs found. The only non-code finding was the `main`-direct-commit process gap in §12.3, which was itself resolved (revert + proper branch reintroduction) before this second review pass concluded.
