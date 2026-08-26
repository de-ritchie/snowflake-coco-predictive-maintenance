# Epics → Stories → Tasks (Kanban Backlog)
## SnowComotive — Predictive Maintenance & OEE Command Center

Status: Draft v0.2 — pending user validation
Traces to: [01-BRD.md](01-BRD.md), [02-FRD.md](02-FRD.md), [03-HLD.md](03-HLD.md), [04-0-LLD.md](04-0-LLD.md) index

---

## 0. Purpose & how to read this doc

This is the missing layer between design (BRD/FRD/HLD/LLD) and execution: a Jira-Kanban-ready backlog. It does **not** re-derive any design decision — every Story cites the FR-ID/LLD section it implements. It does three things the LLD didn't:

1. **Sequences work by build risk and demo-criticality**, not by LLD module number. Epics are tiered **P0 / P1 / P2 / P3**, and within P0 the story order is the literal walking-skeleton build order — confirmed with the user: env spin-up → data generation (thin) → dbt setup/models → first model → semantic view → Streamlit + live tick.
2. **Maps each Story to the human-gated SDLC flow** (Design → Develop → Review → Document, LLD Module 11) so the Kanban board's columns mirror that governance model, not a generic To Do/Doing/Done.
3. **Treats the lifecycle scripts (setup/post-setup/demo/teardown) as versioned artifacts that grow with each tier**, not a single big-bang "ops" epic built once. Setup and post-setup scripts are first written *thin* in P0 (just enough to stand up the walking skeleton) and explicitly revised in place as each later tier adds a component — this matches how you'd actually build it, and gives natural checkpoints for the Reviewer/Documenter skills to re-certify a script after each change instead of only once at the end.

**Two Jira projects, do not conflate** (FRD note under Systems Landscape A5/A10):
- **This backlog → Kanban dev-tracking project (A10)**, work-items below.
- **Jira Service Management project (A5)** — only ever touched by the *deployed* agents at runtime (Module 9), never by this backlog. No Epic/Story here creates SM tickets; SM ticket creation is a *feature* this backlog builds (EPIC-JIRA), not a task tracked *in* SM.

**Priority tiers**:
- **P0 — Walking skeleton.** Env spin-up → thin data gen → dbt (Raw/Std/Cons) → one model (IsolationForest, lower-risk than the AFT/Booster path) → semantic view → post-setup script v1 (semantic view + first agent) → one agent → Streamlit (Overview + Chat) → live tick. Nothing else matters until this runs end-to-end.
- **P1 — Target scope.** Full 3yr+8wk data gen, second model (RUL AFT), post-setup script v2 (updated semantic view + 3 agents), Jira SM ticketing, Forecast/Impact Statement pages, pipeline-run/demo script hardening.
- **P2 — Stretch / bonus.** Explainability tool, MCP swap for Jira, cross-surface demonstration, anything purely for judging-bonus optics.
- **P3 — Teardown.** Deliberately last, not P1: only needed once you're actually done with an environment (end of a dev session, or post-hackathon), so it shouldn't compete with build time earlier.

**SDLC skills — pulled up, per user direction (not deferred to P2):** the 4 custom CoCo skills (Design/Developer/Reviewer/Documenter, LLD Module 11) are built **early**, right after environment spin-up, and then *used* to build every subsequent dbt-model Story in P0 and P1. Snowpark-only stories (data generator, training procs) and script stories (setup/post-setup/demo/teardown) bypass these 4 skills and use the bundled `snowpark-python`/`sql-author`/`snowflake-tasks` skills directly (LLD Module 11, unchanged).

---

## 1. Kanban board shape

**Columns** (mirrors FR-SDLC-02's stage gates):

| Column | Meaning | Exit gate |
|---|---|---|
| Backlog | Not started | — |
| Design | Design agent producing/produced a design note | Human reviews note |
| Dev | Developer agent (or `snowpark-python`/`sql-author`/`snowflake-tasks` skill for non-dbt stories) writing code | PR opened |
| Review | Reviewer agent running tests/acceptance checks | Pass/fail reported |
| Docs | Documenter agent updating `schema.yml`/changelog | — |
| Done | Merged, card closed | Human merge |

Stories that are **not** dbt-model work (Snowpark scripts, Streamlit, agent config, Jira setup, environment/lifecycle scripts) skip the Design/Dev/Review/Docs SDLC-skill gate but still move through the same columns manually — only the *tool used* at Dev differs (per LLD Module 11's dbt-only scope).

**Labels**: `P0`/`P1`/`P2`/`P3` (priority tier), `fr-<id>` (traceability, one or more per story), `sdlc-skill` (stories where a Design/Developer/Reviewer/Documenter skill is the Dev tool) vs `snowpark`/`streamlit`/`agent`/`ops`/`jira` (Dev tool is something else), `script-vN` (which revision of a lifecycle script a story touches — see §2).

**Epics**:

| Epic | Tier | Summary |
|---|---|---|
| EPIC-SDLC | P0 (built first, used throughout) | Author the 4 CoCo SDLC skills |
| EPIC-SKELETON | P0 | Env spin-up → thin data gen → dbt Raw/Std/Cons → IsolationForest → semantic view → post-setup script v1 (semantic view + 1 agent) → Streamlit (Overview+Chat) → live tick |
| EPIC-FULLDATA | P1 | Full 3yr+8wk data gen, all failure modes, crew-capacity mechanism, fallback patch if needed; setup script updated to full FR-DG-12 invocation |
| EPIC-RUL | P1 | RUL AFT model (raw Booster path), priority score, Forecast OEE; pipeline-run script updated for 2-model training order |
| EPIC-PERSONAS | P1 | 3 persona agents, tool differentiation, Impact Statement page; post-setup script v2 (updated semantic view + 3 agents) |
| EPIC-JIRA | P1 | Jira SM integration, ticket creation tool, duplicate-check; setup script updated for Jira provisioning |
| EPIC-OPS-HARDEN | P1 | Harden setup/pipeline-run/demo scripts to their full, idempotent, FR-OPS-0x-complete form |
| EPIC-STRETCH | P2 | Explainability tool, MCP swap, cross-surface demo, multi-agent handoff (if time) |
| EPIC-TEARDOWN | P3 | Teardown script — last priority, only needed when actually done with an environment |

---

## 2. Script evolution across tiers (cross-cutting view)

Rather than one "ops" epic built once, each lifecycle script is a living artifact revised at the tier boundary where a new component needs to be added to it. This table is the index — the actual authoring/revision tasks live inside the Epic that introduces the new component.

| Script | P0 content (EPIC-SKELETON) | P1 content (added by) | P2 (added by) | P3 |
|---|---|---|---|---|
| **Setup script** | Role/warehouse/database/schemas/stage (S-ENV-1/2) + thin data-gen invocation (S-DATA-1) | + full FR-DG-12 data-gen invocation & reuse-dataset logic (EPIC-FULLDATA, S-OPS-SETUP-2) + Jira SM project provisioning (EPIC-JIRA, S-JIRA-1) | — | — |
| **Post-setup script** | v1: creates semantic view (S-SEM-1) + 1 agent (S-AGENT-1) (EPIC-SKELETON, S-OPS-POST-1) | v2: updated semantic view (more entities) + 3 agents + ticket tool wiring + Streamlit full deploy (EPIC-PERSONAS, S-OPS-POST-2; EPIC-JIRA, S-JIRA-3) | + explainability tool wiring, if validated (EPIC-STRETCH) | — |
| **Pipeline-run script** | Thin `dbt run`/`dbt test`, single-model training call (EPIC-SKELETON, part of S-MODEL-2/3) | Full 2-phase form with training step ordered between (FR-OPS-02/02a/02b) once RUL model exists (EPIC-RUL, S-OPS-PIPE-2) | — | — |
| **Demo script** | Single manual tick trigger (S-DEMO-1) | Full drip-feed set + both production modes (a)/(b) (EPIC-FULLDATA, S-DATA-7) | — | — |
| **Teardown script** | — | — | — | Full FR-OPS-05, idempotency check across all scripts (EPIC-TEARDOWN, S-TEARDOWN-1) |

---

## 3. EPIC-SDLC — Author the 4 CoCo SDLC skills (P0, built first)

Traces to: FR-SDLC-01/02/03, LLD Module 11.

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-SDLC-1**: Author Design skill | T1: Draft `SKILL.md` (input: Jira story; reads LLD module; outputs design note, no invented architecture). T2: Test against a throwaway story. | Module 11 §1 |
| **S-SDLC-2**: Author Developer skill | T1: Draft `SKILL.md` (writes dbt `.sql`+`.yml`, follows Module 1/3/4 naming/materialization conventions). T2: Test against a throwaway story. | Module 11 §2 |
| **S-SDLC-3**: Author Reviewer skill | T1: Draft `SKILL.md` (runs `dbt run --select <model>+`/`dbt test`, checks acceptance criteria + named invariants e.g. leakage-safety, incremental-refresh). T2: Test against a throwaway PR. | Module 11 §3 |
| **S-SDLC-4**: Author Documenter skill | T1: Draft `SKILL.md` (updates `schema.yml` descriptions + changelog on merge). T2: Test against a throwaway merge. | Module 11 §4 |
| **S-SDLC-5**: Publish all 4 as reusable skills | T1: Confirm frontmatter/format matches CoCo skill packaging. T2: Add short README cross-links between them (Design→Dev→Review→Docs). | FR-SDLC-03 |

**Definition of Done**: all 4 skills exist and have each been exercised at least once against a real (non-throwaway) Story before EPIC-SKELETON's dbt-model stories begin.

---

## 4. EPIC-SKELETON — Walking skeleton, P0 (the literal next-steps order)

Build order as agreed: **env spin-up → data gen (thin) → dbt setup/models → first model → semantic view → post-setup script v1 (semantic view + 1 agent) → Streamlit + live tick.** Every dbt-model Story here is built via the Design→Dev→Review→Docs skill chain from EPIC-SDLC.

### 4.1 Environment spin-up (setup script v0 — SQL only, no data yet)

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-ENV-1**: Create role, warehouse, database, schemas | T1: `CREATE ROLE`, `CREATE WAREHOUSE` (XS, auto-suspend). T2: `CREATE DATABASE` + `RAW`/`STD`/`CONS`/`FEAST` schemas. T3: Grants. | FR-OPS-01 (partial), LLD Module 10 §1 (SQL block only) |
| **S-ENV-2**: Create Stage | T1: `CREATE STAGE` with Parquet file format. | FR-OPS-01, Module 10 §1 |

Explicitly **deferred**: Jira SM project creation, reuse-existing-dataset logic (NFR-05), teardown. These get added to the setup script later (see §2 table) as the components they depend on come online.

### 4.2 Data generation — thin (setup script v1: adds data-gen invocation)

Pulled out as its own step, prioritized immediately after env spin-up and before dbt project scaffolding — dbt's Standardized/Consumption models can't run until Raw has real rows, so data must land first.

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-DATA-1**: Thin Snowpark generator | T1: Write a minimal generator run — 1-3 machines, a few weeks of data, not the full 3yr/crew-capacity mechanism (that's EPIC-FULLDATA) — just enough rows to exercise the pipeline shape. T2: Write to Parquet. | FR-DG-12 (thin invocation only), Module 2 (deferred: full algorithm) |
| **S-DATA-2**: Upload + load into Raw | T1: Upload Parquet to S-ENV-2's Stage. T2: `COPY INTO` `RAW.EQUIPMENT`, `RAW.SENSOR_READING`, `RAW.CMMS_LOG` (defer order/inventory/calendar to EPIC-FULLDATA if not needed for the first model). T3: Fold this invocation into the setup script (setup script v1). | FR-OPS-01, Module 1 (RAW section) |

**Definition of Done**: `RAW.SENSOR_READING` has real rows in Snowflake before any dbt model is written.

### 4.3 dbt project setup + SQL models (thin slice)

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-DBT-1**: Scaffold dbt project | T1: `dbt init`, profiles pointed at S-ENV-1's role/warehouse/db. T2: Configure schema tags (Raw/Standardized/Consumption) per FR-PL-08. | FR-PL-01/02/05 |
| **S-DBT-2**: Standardized sensor model | T1: `STD.SENSOR_READING` dynamic table with leakage-safe ASOF joins (`hours_since_last_service`, `hours_since_install`). | FR-PL-02/03, Module 3 §1 |
| **S-DBT-3**: Consumption lean fact | T1: `CONS.FCT_SENSOR_READING` dynamic table (pass-through). T2: `CONS.DIM_EQUIPMENT`, `CONS.DIM_SENSOR_BASELINE`. | FR-PL-05, Module 3 §2, Module 1 (CONS section) |
| **S-DBT-4**: dbt tests | T1: not_null/relationships tests on key columns per FR-PL-08. | FR-PL-08 |

**Definition of Done**: `dbt run && dbt test` succeeds end-to-end on the thin dataset; `CONS.FCT_SENSOR_READING` has real rows queryable in Snowsight.

### 4.4 First model — IsolationForest only (defer RUL/AFT to EPIC-RUL)

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-MODEL-1**: FEAST macro + inference feature table | T1: Write `sensor_rolling_features` macro (rolling windows + normalization, wide-format pivot). T2: `FEAST.FCT_SENSOR_FEATURES_INFERENCE` dynamic table. **Skip `FCT_SENSOR_FEATURES_TRAIN`'s frozen-snapshot distinction for the very first cut if time-pressed — training can read the inference table directly for a first pass; split them out before EPIC-FULLDATA if reproducibility becomes an issue.** | FR-FS-01, Module 4 §1/§2 |
| **S-MODEL-2**: Train IsolationForest | T1: Python stored procedure or notebook cell training on the thin dataset. T2: Log to Model Registry with `sample_input_data`, confirm default `volatility=IMMUTABLE`. T3: Fold the training call into a first-cut pipeline-run script (pipeline-run script v1, single-model). | FR-FS-00/00a/00f, Module 5 §1 |
| **S-MODEL-3**: Inference dynamic table | T1: `CONS.FCT_ANOMALY_RESULT` dynamic table calling `MODEL(...)!predict/!decision_function(...)`. T2: **Spike/verify**: does `INITIALIZE = on_create` correctly backfill history (FR-FS-00e)? Does the dynamic table achieve true incremental refresh, or fall back to full rescore (FR-FS-09)? Document the answer — do not assume. | FR-FS-00b/00e, FR-FS-09 (critical verification) |

**Definition of Done**: a manually-triggered new sensor tick produces exactly one new row in `CONS.FCT_ANOMALY_RESULT` without visibly rescoring history (or, if it does rescore, that's documented as an accepted cost tradeoff at this data volume — not silently ignored).

### 4.5 Semantic view + post-setup script v1 (semantic view + first agent)

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-SEM-1**: Create semantic view | T1: Map `Machine`, `SensorReading`, `AnomalyResult` (minimum for the skeleton — add `MaintenanceEvent`/`Order`/`Inventory`/`OEEMetric`/`PriorityScore` in EPIC-FULLDATA/RUL/PERSONAS as those tables exist). T2: Define relationships. | FR-SA-01, Module 6 |
| **S-SEM-2**: One verified query | T1: Write and validate one verified query ("what's the current anomaly score for machine X") against real data. | FR-SA-01 (minimum viable subset) |
| **S-AGENT-1**: One Cortex Agent (Supervisor-equivalent, Analyst tool only) | T1: `CREATE AGENT` with `cortex_analyst_text_to_sql` tool against S-SEM-1's semantic view. Defer `explain_prediction`/`create_jira_ticket` tools and the other 2 personas to P1/P2. | FR-SA-02 (Supervisor subset), Module 7 §1 |
| **S-OPS-POST-1**: Write post-setup script v1 | T1: New script (FR-OPS-03 partial) that runs `CREATE SEMANTIC VIEW` (S-SEM-1) + `CREATE AGENT` (S-AGENT-1) — this is the first version of the post-setup script; it gets revised, not replaced, in EPIC-PERSONAS. | FR-OPS-03, Module 10 §5 (partial) |

### 4.6 Streamlit (Overview + Chat only) + live tick

| Story | Task/Sub-tasks | Traces to |
|---|---|---|
| **S-APP-1**: Streamlit shell + Overview page | T1: App shell, sidebar (no persona switcher yet — single agent). T2: Health status cards from `cons.fct_anomaly_result`. | FR-CC-01, Module 8 §0/§1 (partial) |
| **S-APP-2**: Agent Chat page | T1: Chat panel wired to S-AGENT-1. | FR-CC-05, Module 8 §4 |
| **S-OPS-POST-1b**: Fold Streamlit deploy into post-setup script v1 | T1: `snow streamlit deploy` call added to the same script as S-OPS-POST-1. | FR-OPS-03 |
| **S-DEMO-1**: Live tick — manual trigger (demo script v1) | T1: Task for `COPY INTO` on Raw sensor landing (FR-PL-04). T2: One pre-generated held-back tick file + manual upload script (thin version of FR-PL-04b — defer the full 30-day drip-feed set to EPIC-FULLDATA). T3: "Inject next tick" button/script triggers upload + `EXECUTE TASK`. | FR-PL-04/04b, FR-CC-06, Module 10 §6 (thin version) |

**Definition of Done — the walking skeleton is complete when**: a manual tick injection visibly changes a chart in Streamlit within the demo session, and the agent chat answers a real question grounded in the semantic view. This is the checkpoint before any P1 work starts.

---

## 5. EPIC-FULLDATA — Full data generation (P1)

Traces to: FR-DG-01 through 12, LLD Module 2 (full algorithm, not the thin S-DATA-1 stand-in). Also updates the setup script (v1 → v2, full FR-DG-12 invocation + reuse-dataset logic).

| Story | Task/Sub-tasks |
|---|---|
| **S-DATA-3**: Full calendar + order series | Calendar dim (§1); one continuous 3yr+8wk order series per product/variant with the two-beat look-ahead (§7). |
| **S-DATA-4**: Machine baselines + failure-mode signatures | Populate `CONS.DIM_SENSOR_BASELINE` for all 3 machines; implement the 5 failure-mode sensitivity table (§3). |
| **S-DATA-5**: Health degradation + restoration formula | Weibull decay + noise (§4); PM/breakdown restoration (§5). |
| **S-DATA-6**: Crew-capacity contention mechanism | Weekly/weekend slot logic, background plant draw, priority tie-break by order volume (§5, FR-DG-05a). |
| **S-DATA-7**: Right-censoring labels | Correct `y_lower`/`y_upper` per FR-DG-11 (§6). |
| **S-DATA-8**: Inventory + spare parts generation | Weekly snapshots tied to repair events (§7, FR-DG-08). |
| **S-DATA-9**: 30-day drip-feed split | Full held-back tick file set for progressive release (§8, FR-PL-04b) — supersedes S-DEMO-1's single-file stand-in; updates demo script to v2. |
| **S-DATA-10**: Run generator, verify demo narrative lands | Run full generation; check whether Engine Head is convincingly overdue. **If not**, apply the accepted fallback hand-patch (§10) — do not re-roll seeds indefinitely. |
| **S-OPS-SETUP-2**: Update setup script to v2 | T1: Swap thin S-DATA-1 invocation for the full FR-DG-12 generator call. T2: Add `--reuse-dataset-path`/`--seed` flags (NFR-05). | FR-OPS-01, NFR-05, Module 10 §1 (full) |

---

## 6. EPIC-RUL — Second model: RUL AFT (P1)

Traces to: FR-FS-02/03/04/05, LLD Module 4 §4, Module 5 §2/§3. Also updates the pipeline-run script (v1 → v2, 2-phase + training-in-between).

| Story | Task/Sub-tasks |
|---|---|
| **S-RUL-0 (do this first, it's the riskiest unknown)**: Spike — raw `xgboost.Booster` via Model Registry generic/custom path, called via `MODEL(...)!predict()` in SQL | Toy model, toy data — confirm the whole "no stored procedure for RUL inference" design actually works before building the real pipeline around it. If it fails, fall back to procedure-based RUL inference and update HLD/Module 1/4/5 accordingly. |
| **S-RUL-1**: Training spine + split | `FEAST.SPINE_MAINTENANCE_CYCLE`, `TRAINING_DATASET_RUL` with time-based `dataset_split` (Module 4 §4a). |
| **S-RUL-2**: Wire anomaly output into RUL features | Join `is_anomaly`/`anomaly_score` into `TRAINING_DATASET_RUL` (Module 4/5's flagged open item). |
| **S-RUL-3**: Train RUL AFT model | DMatrix construction, `objective=survival:aft`, explicit `volatility=IMMUTABLE` logging (Module 5 §2). |
| **S-RUL-4**: Evaluation | Concordance index + MAE on uncensored subset, logged as Model Registry metrics (Module 5 §3). |
| **S-RUL-5**: RUL inference dynamic table | `CONS.FCT_RUL_PREDICTION` (Module 1, FR-FS-07). |
| **S-RUL-6**: Priority score | `CONS.FCT_PRIORITY_SCORE` dynamic table, HLD §5 formula (FR-FS-08). |
| **S-RUL-7**: Forecast OEE page + Priority Queue page | Streamlit (Module 8 §2/§3, FR-CC-02/03). |
| **S-OPS-PIPE-2**: Update pipeline-run script to v2 | T1: Split into phase 1 (`--exclude tag:inference+`) / training / phase 2 (`--select tag:inference+`) per FR-OPS-02/02a/02b, since inference tables now reference 2 models by name at creation time. | FR-OPS-02/02a/02b, Module 10 §2-4 |

---

## 7. EPIC-PERSONAS — 3 personas & Impact Statement (P1)

Updates the post-setup script (v1 → v2).

| Story | Task/Sub-tasks |
|---|---|
| **S-PERSONA-1**: Production Planner agent | `Analyst` + `explain_prediction`(if ready) + `request_jira_ticket` tools, escalation-framed instructions (Module 7 §4). |
| **S-PERSONA-2**: Plant Manager agent | `Analyst`-only, read-only rollup instructions. |
| **S-PERSONA-3**: Persona switcher in Streamlit | Sidebar control changes active agent + tool availability (FR-CC-04). |
| **S-IMPACT-1**: Impact Statement page | 4 metric cards computed from real pipeline/model output, not hardcoded (FR-CC-07, BRD §8). |
| **S-OPS-POST-2**: Update post-setup script to v2 | T1: Extend S-OPS-POST-1's script — update `CREATE SEMANTIC VIEW` for the fuller entity set (Order/Inventory/OEEMetric/PriorityScore, now that EPIC-FULLDATA/RUL built those tables), add `CREATE AGENT` for S-PERSONA-1/2, redeploy Streamlit with persona switcher. | FR-OPS-03, Module 10 §5 (full) |

---

## 8. EPIC-JIRA — Jira Service Management integration (P1)

Updates the setup script (adds Jira SM provisioning) and post-setup script (adds ticket tool wiring).

| Story | Task/Sub-tasks |
|---|---|
| **S-JIRA-1**: Provision SM project + auth | Create/verify SM project (idempotent, FR-OPS-01); Secret + Network Rule + External Access Integration (Module 9 §3). Adds to the setup script. |
| **S-JIRA-2**: `SP_CREATE_JIRA_TICKET` procedure | Field mapping (Module 9 §1) + duplicate-check JQL logic (FR-JR-04, Module 9 §2). |
| **S-JIRA-3**: Wire tool into Supervisor + Planner agents | `create_jira_ticket`/`request_jira_ticket` tool specs (Module 7 §3) — folds into post-setup script v2 alongside S-OPS-POST-2. |
| **S-JIRA-4**: Streamlit tool-call confirmation UI | Distinct wording for `CREATED` vs `ALREADY_OPEN`/`RECENTLY_CLOSED` (Module 8 §4). |

---

## 9. EPIC-OPS-HARDEN — Script hardening & idempotency (P1)

Everything else needed to make the incrementally-built scripts fully match their FR-OPS spec and be safely re-runnable out of order — a cleanup/hardening pass over the versions built incrementally in §2, not new functionality.

| Story | Task/Sub-tasks |
|---|---|
| **S-OPS-HARDEN-1**: Idempotency pass across setup/pipeline/post-setup/demo scripts | Confirm `CREATE OR REPLACE`/`IF NOT EXISTS` everywhere; confirm `COPY INTO` load-history dedup; confirm phase-2-before-training fails loudly, not silently (FR-OPS-06). |
| **S-OPS-HARDEN-2**: Full demo script (both production modes) | Background pre-demo drip upload process (mode a) alongside the on-demand trigger (mode b) already built in S-DEMO-1/EPIC-FULLDATA (FR-PL-04b). |

---

## 10. EPIC-STRETCH — Bonus/optics (P2, only if P0+P1 demo is solid)

| Story | Task/Sub-tasks |
|---|---|
| **S-STRETCH-1**: Explainability tool | Run Module 5 §4's validation plan for real; if it passes, wire `explain_prediction` tool (Module 7 §2) into post-setup script; if not, document the fallback and drop it from scope cleanly. |
| **S-STRETCH-2**: MCP swap for Jira | Replace direct REST call with an MCP Jira connector — ticks the guidelines' explicit "Connecting to additional sources via MCP" bullet. |
| **S-STRETCH-3**: Cross-surface demonstration | Show the same agent working natively in Snowsight Cloud Agents, not just embedded in Streamlit. |
| **S-STRETCH-4**: Judging-alignment checklist doc | One-page mapping of every submission-guideline bullet (4 lifecycle phases, 6 recommended tasks, 7 ingenuity bullets) to the specific artifact/screenshot/demo-beat that covers it — useful for writing the final submission, not just dev tracking. |

---

## 11. EPIC-TEARDOWN — Teardown script (P3, deliberately last)

| Story | Task/Sub-tasks |
|---|---|
| **S-TEARDOWN-1**: Full teardown script | Drop database (cascades schemas/tables/stage/dynamic tables/semantic view), drop 3 agents, drop Streamlit app, drop role, drop warehouse (FR-OPS-05). Jira SM project/secret explicitly **not** dropped (external system, manual cleanup). | FR-OPS-05, Module 10 §7 |
| **S-TEARDOWN-2**: Re-run idempotency check | Confirm setup can be re-run cleanly after teardown (round-trip test), per FR-OPS-06. |

---

## 12. Next step

This doc is the source content for the actual Jira Kanban board. No Jira connection exists yet in this environment (no site URL/project key/auth configured). Once you provision the Kanban project (A10) and share connection details, this doc's Epics/Stories/Tasks can be pushed in as real issues (via Jira REST API or an MCP Jira connector) rather than re-typed by hand.
