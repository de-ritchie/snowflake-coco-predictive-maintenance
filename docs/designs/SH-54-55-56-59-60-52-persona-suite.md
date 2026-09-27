# Design: Persona Suite — Planner/Plant Manager agents, ticket wiring, landing page, ticket-confirmation UI

Status: **Design frozen** (brainstorm confirmed by user, 2026-09-27) → `Developer-agent` build → **`Reviewer-agent` clean PASS (2026-09-27), all 9 invariants held, including independent live re-verification of agent tool lists, idempotency, and a live end-to-end ticket-creation run** (see §12 for results). `Documenter-agent` reconciliation complete.
**Stories**: SH-54 (S-PERSONA-1: Production Planner agent), SH-55 (S-PERSONA-2: Plant Manager agent), SH-56 (S-JIRA-3: wire ticket tool into Supervisor + Planner), SH-59 (S-PERSONA-3: persona landing page + persistence — supersedes the sidebar-switcher framing from `docs/05-Epics.md`'s original one-liner, see §1a), SH-60 (S-OPS-POST-2: post-setup script v2), SH-52 (S-JIRA-4: ticket-call confirmation UI)
**Branch**: `feature/SH-3-54-persona-suite`
**Traces to**: [docs/04-7-LLD.md](../04-7-LLD.md) (Module 7, Agent Tool Specs, §3/§4), [docs/04-8-LLD.md](../04-8-LLD.md) (Module 8, Streamlit App, §0/§4), [docs/03-HLD.md](../03-HLD.md) §5 (line ~211, persona-switcher framing — see §1a deviation footnote below), [docs/designs/SH-58-simulated-ticket-store.md](SH-58-simulated-ticket-store.md) (ticket-store procedure contracts), [docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md](SH-27-28-30-31-32-33-semantic-view-agent-chat.md) (multi-story bundling precedent, existing Chat page/post-setup script), [docs/05-Epics.md](../05-Epics.md) §7/§8 (EPIC-PERSONAS/EPIC-JIRA one-line story descriptions)

**Excluded from this bundle**: SH-57 (Impact Statement page — orthogonal static KPI cards, separate story). `explain_prediction` tool — `SP_EXPLAIN_PREDICTION` does not exist yet (EPIC-STRETCH S-STRETCH-1, not built); Module 7 §4's persona→tool table lists it as "if ready" for Supervisor/Planner, and it is not ready, so neither new agent nor the revised Supervisor agent gets it in this story. This is not a deviation, it's Module 7's own documented conditional resolving to "not yet."

---

## 1. Scope

Six stories delivered together because they're one coherent slice — a live agent object is useless without a UI that picks it, and the UI's persona list is meaningless without the agents existing:

1. **SH-54 / SH-55**: two new Cortex Agent objects, `snowcomotive.cons.production_planner_agent` and `snowcomotive.cons.plant_manager_agent`, alongside the existing `maintenance_supervisor_agent`.
2. **SH-56**: `create_jira_ticket` tool wired into `maintenance_supervisor_agent` (it currently has Analyst only) and into `production_planner_agent` as `request_jira_ticket` — both pointing at `SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET` (SH-58's simulated ticket store).
3. **SH-60**: `scripts/07_post_setup.sql` revised in place (per this project's "revise, don't replace" convention, `docs/05-Epics.md` §2) to create all 3 agents. The semantic view already has the fuller Order/Inventory/OEEMetric/PriorityScore entity set (§2 below confirms this is already done, nothing to add there).
4. **SH-59**: a new persona-picker landing page, inserted first in nav, gating every other page until a persona is chosen; persistence via `st.session_state` + a `?persona=` query param.
5. **SH-52**: `pages/4_Chat.py` renders visually distinct confirmations for `CREATED` vs. `ALREADY_OPEN`/`RECENTLY_CLOSED` tool-call results.
6. Chat page also becomes persona-aware: it currently hardcodes `maintenance_supervisor_agent` — this bundle makes it call whichever agent the picked persona maps to, and live-introspects that agent's actual tools (§6) to render an accurate capability list, rather than hardcoding a second copy of the persona→tool table.

---

## 1a. HLD deviation footnote (dated, per this project's established convention)

`docs/03-HLD.md` §5 (line ~211) currently reads: *"Persona switcher is a sidebar control, not a separate page."* Module 8 §0 similarly describes a sidebar `st.radio`/`st.selectbox`.

**Deviation (confirmed with user, 2026-09-27, this design session)**: persona selection is instead a dedicated landing page, inserted as the new first page in nav, with a hard gate — any other page loaded without a persona already chosen (e.g., direct URL navigation) redirects the user back to the picker rather than silently defaulting or offering an in-page sidebar control. Rationale: a forced first-run choice is a clearer demo beat than a sidebar control that's easy to miss, and Streamlit's native multipage nav makes "first page in the list" a natural landing spot. The original HLD text is **not rewritten** — this footnote is the record of the deviation, per this repo's existing pattern (e.g. SH-53's Module 9 §3 footnote, SH-58's Module 7 §3 footnote). A future `Documenter-agent` pass may add a parallel footnote directly in `docs/03-HLD.md`/`docs/04-8-LLD.md` themselves; not done here.

---

## 2. Ground truth check: is SH-60's semantic-view update already done?

**Yes — already done, nothing to add.** `scripts/07_post_setup.sql` (read live, this session) already declares the fuller entity set: `machine`, `product`, `sensor_reading`, `anomaly_result`, `maintenance_event`, `order_`, `inventory_fg`, `inventory_spare`, `oee_metric`, **and `priority_score`** (with its own `FACTS`/`DIMENSIONS`/`METRICS` entries and two dedicated `AI_VERIFIED_QUERIES`: `priority_score_ranking`, `priority_score_demand_survivability`). This matches SH-60's task description ("update `CREATE SEMANTIC VIEW` for the fuller entity set... now that EPIC-FULLDATA/RUL built those tables") exactly — it was evidently done incrementally in an earlier story (SH-47's priority-score work) rather than held back for this bundle, the same "already-built" pattern SH-43 hit before. **SH-60's actual remaining scope in this story is therefore just the agent-object additions** (§4) — no semantic view SQL changes needed.

---

## 3. Architecture already confirmed with the user (not re-litigated here)

- **No new Snowflake roles.** `SNOWCOMOTIVE_ROLE` remains the sole session role for every persona. The capability boundary is enforced entirely by which `tools` are configured on each Cortex Agent object.
- **Persona selection is a pure Streamlit app-level construct**, not a Snowflake auth switch.

---

## 4. `CREATE OR REPLACE AGENT` — all 3 agents, as they will appear in `scripts/07_post_setup.sql` v2

### 4.1 `maintenance_supervisor_agent` — revised in place, gains `create_jira_ticket`

Only the `instructions.orchestration` text and `tools`/`tool_resources` blocks change from the current live version (read this session, §current file lines 244-283); `instructions.response` is unchanged (still Analyst-grounded, no-fabrication guardrail).

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
    anomalies, maintenance events, OEE, orders, inventory, priority score,
    and predicted remaining-useful-life (RUL), grounded strictly in the
    Analyst tool's query results against the semantic view. Never fabricate
    a health, anomaly, OEE, inventory, priority-score, or RUL value. If
    data for a requested machine or time period is missing or stale, say
    so explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, inventory,
    priority score, or predicted remaining-useful-life (RUL). Only call
    create_jira_ticket when the user explicitly asks to file, create, or
    dispatch a maintenance ticket for a specific machine -- never
    proactively, even if a machine looks at-risk. Report whichever status
    the tool actually returns (CREATED, ALREADY_OPEN, or RECENTLY_CLOSED)
    -- never assume or imply a fresh ticket was filed if the tool returned
    an existing one. Explaining *why* a specific prediction was made
    (a feature-level breakdown) is not enabled yet; if asked, say so
    explicitly rather than guessing at a feature-level rationale --
    reporting a predicted value returned by Analyst is fine and expected,
    that is not the same capability.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, inventory, and priority ranking using the SnowComotive semantic view."
  - tool_spec:
      type: "generic"
      name: "create_jira_ticket"
      description: "Creates a Jira Service Management ticket for a machine needing maintenance, unless one already exists (open or recently closed) -- in which case it returns the existing ticket instead of creating a duplicate. Only call when the user explicitly asks to file/create/dispatch a ticket -- never automatically."
      input_schema:
        type: "object"
        properties:
          equipment_id: { type: "string", description: "Machine needing maintenance" }
          predicted_rul_hours: { type: "number", description: "Predicted remaining useful life, from the RUL model" }
          priority_score: { type: "number", description: "Current priority score, 0-100" }
          root_cause_summary: { type: "string", description: "Plain-language root cause, grounded in sensor/model data" }
          requested_by_persona: { type: "string", description: "Maintenance Supervisor or Production Planner" }
        required: ["equipment_id", "predicted_rul_hours", "priority_score", "root_cause_summary", "requested_by_persona"]
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
  create_jira_ticket:
    type: "procedure"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
    identifier: "SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET"
$$;
```

### 4.2 `production_planner_agent` — new (SH-54, ticket tool per SH-56)

```sql
CREATE OR REPLACE AGENT snowcomotive.cons.production_planner_agent
  PROFILE = '{"display_name": "SnowComotive Production Planner Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Production Planner Agent for a predictive-
    maintenance and OEE command center. Answer questions about demand
    (orders), finished-goods and spare-parts inventory, OEE, priority
    score, and predicted remaining-useful-life (RUL), grounded strictly in
    the Analyst tool's query results against the semantic view. Never
    fabricate a health, OEE, inventory, priority-score, or RUL value. If
    data for a requested machine, product, or time period is missing or
    stale, say so explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for questions about demand/orders, finished-goods
    and spare-parts inventory, OEE, and priority/RUL signals -- frame
    answers around production-planning impact (will we meet demand, what
    is the OEE or supply risk) rather than raw sensor detail. Only call
    request_jira_ticket when the user explicitly asks to escalate or
    request maintenance attention on a machine -- never proactively, even
    if a machine looks at-risk. Frame this call as requesting/escalating
    to the Maintenance Supervisor, not as directly dispatching a repair --
    you plan, you do not do hands-on maintenance work. Report whichever
    status the tool actually returns (CREATED, ALREADY_OPEN, or
    RECENTLY_CLOSED) -- never assume or imply a fresh escalation was filed
    if the tool returned an existing one.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, inventory, and priority ranking using the SnowComotive semantic view."
  - tool_spec:
      type: "generic"
      name: "request_jira_ticket"
      description: "Requests a Jira Service Management ticket to escalate a machine needing maintenance attention to the Maintenance Supervisor, unless one already exists (open or recently closed) -- in which case it returns the existing ticket instead of creating a duplicate. Only call when the user explicitly asks to escalate/request a ticket -- never automatically."
      input_schema:
        type: "object"
        properties:
          equipment_id: { type: "string", description: "Machine needing maintenance" }
          predicted_rul_hours: { type: "number", description: "Predicted remaining useful life, from the RUL model" }
          priority_score: { type: "number", description: "Current priority score, 0-100" }
          root_cause_summary: { type: "string", description: "Plain-language root cause, grounded in sensor/model data" }
          requested_by_persona: { type: "string", description: "Maintenance Supervisor or Production Planner" }
        required: ["equipment_id", "predicted_rul_hours", "priority_score", "root_cause_summary", "requested_by_persona"]
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
  request_jira_ticket:
    type: "procedure"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
    identifier: "SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET"
$$;
```

### 4.3 `plant_manager_agent` — new (SH-55, Analyst-only, no ticketing)

```sql
CREATE OR REPLACE AGENT snowcomotive.cons.plant_manager_agent
  PROFILE = '{"display_name": "SnowComotive Plant Manager Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Plant Manager Agent, a read-only rollup
    persona for OEE, machine health, and inventory oversight, grounded
    strictly in the Analyst tool's query results against the semantic
    view. Never fabricate a health, OEE, inventory, priority-score, or RUL
    value. If data for a requested machine or time period is missing or
    stale, say so explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for any question about machine health, anomalies,
    maintenance history, OEE, orders, inventory, or priority score. You
    have no ticketing tool -- never suggest filing, creating, or
    escalating a ticket; if asked, say that ticketing is not available for
    this persona and redirect the user to the Production Planner or
    Maintenance Supervisor persona instead.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, inventory, and priority ranking using the SnowComotive semantic view."
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
$$;
```

`Documenter-agent` should later add a Module 7 §4 footnote once these ship, noting `explain_prediction` was omitted from both Supervisor's and Planner's actual `tools` list vs. the LLD table's "if ready" — because it isn't ready (§1's exclusion note).

---

## 5. Persona landing page (SH-59)

### 5.1 File and nav position

New file `oee_command_center_app/pages/0_Choose_Persona.py` — Streamlit's classic multipage nav sorts by the leading number, so `0_` puts it first, ahead of `1_Overview.py`. (Existing pages keep their numbers; no renumbering needed.)

### 5.2 Persona registry (single source of truth for page code)

```python
PERSONAS = {
    "supervisor": {
        "label": "Maintenance Supervisor",
        "agent_name": "maintenance_supervisor_agent",
        "blurb": "Hands-on machine health, root-cause, and maintenance dispatch.",
    },
    "planner": {
        "label": "Production Planner",
        "agent_name": "production_planner_agent",
        "blurb": "Demand, inventory, and OEE risk -- escalates maintenance requests, doesn't dispatch directly.",
    },
    "plant_manager": {
        "label": "Plant Manager",
        "agent_name": "plant_manager_agent",
        "blurb": "Read-only OEE/health/inventory rollup -- no ticketing.",
    },
}
```

Lives in `streamlit_app.py` (imported by both the picker page and the Chat page) alongside `get_connection`/`render_sidebar` — same "shared module imported by page scripts" convention already established.

### 5.3 Picker page behavior

- Title: "Choose your persona." One-line explainer: "Pick the role you're viewing this command center as -- this determines which agent answers your questions in Chat and whether you can file maintenance tickets."
- Three `st.container(border=True)` cards (or `st.columns(3)`), one per `PERSONAS` entry, each showing `label` + `blurb` + a button ("Continue as {label}").
- On click: set `st.session_state["persona"] = key`, set `st.query_params["persona"] = key`, then `st.switch_page("pages/1_Overview.py")`.
- If `st.session_state.get("persona")` is already set when this page loads (e.g. user navigates back to it), show the same 3 cards so they can switch, plus a note "Currently viewing as: {label}" at the top -- switching is always allowed, not just a one-time choice.

### 5.4 Gate on every other page (hard gate, force back to picker — user's explicit decision)

A small shared helper in `streamlit_app.py`:

```python
def require_persona() -> str:
    """Call at the top of every page except the picker itself. Returns the
    active persona key, or halts the page with a redirect prompt if none is
    set yet -- restores from ?persona= query param first (survives a hard
    refresh), matching the ?demo=1 convention.
    """
    if "persona" not in st.session_state:
        param = st.query_params.get("persona")
        if param in PERSONAS:
            st.session_state["persona"] = param
    if "persona" not in st.session_state:
        st.info("Please choose a persona to continue.")
        if st.button("Choose persona"):
            st.switch_page("pages/0_Choose_Persona.py")
        st.stop()
    return st.session_state["persona"]
```

Every existing page (`1_Overview.py`, `2_Prioritization.py`, `3_Forecast_OEE.py`, `4_Chat.py`) gets one added line, `require_persona()`, right after `render_sidebar()` -- no other page logic changes (all 3 personas can view all pages; only Chat's *tools* differ, matching Module 8 §0's "persona switch changes actions, not the page list" intent, just relocated from a sidebar control to this landing-page + gate mechanism).

### 5.5 Persistence

`st.session_state["persona"]` is the in-run source of truth; `st.query_params["persona"]` mirrors it so a hard refresh or a shared link preserves the selection -- exactly the `?demo=1` precedent already in `streamlit_app.py`.

---

## 6. Chat page becomes persona-aware + live tool introspection (SH-59/SH-56 integration point)

### 6.1 Agent selection

`pages/4_Chat.py`'s hardcoded `AGENT_NAME = "maintenance_supervisor_agent"` (line 29) is replaced with a lookup: `persona = require_persona()`, then `AGENT_NAME = PERSONAS[persona]["agent_name"]`. The REST path (`AGENT_RUN_PATH`) is built from that resolved name instead of a module-level constant.

### 6.2 Live capability introspection (user's explicit choice: live, not a static dict)

Rather than hardcoding a second copy of the persona→tool table in the Streamlit page (which could drift from what's actually configured on the live agent object — the exact risk flagged and avoided here), the Chat page queries the agent object itself:

```python
@st.cache_data(ttl=300)
def get_agent_tool_names(agent_name: str) -> list[str]:
    """Live introspection, not a hardcoded persona->tool dict: parses
    DESCRIBE AGENT's specification output so the capability list can never
    drift from what's actually wired on the live agent object."""
    conn = get_connection()
    df = conn.query(f"DESCRIBE AGENT snowcomotive.cons.{agent_name}", ttl=300)
    # Flagged assumption (Developer-agent must verify live): DESCRIBE AGENT
    # is expected to return a row with a "specification" (or similarly
    # named) column holding the same YAML/JSON body passed to
    # `FROM SPECIFICATION $$ ... $$` -- parse its top-level `tools[].
    # tool_spec.name` list. Exact column name/shape not verifiable without
    # a live session in this design conversation; §9's flagged item.
    ...
```

A small **static display-name lookup** maps the raw tool name to a human-readable one-line capability description for rendering (this part is unavoidably static — `DESCRIBE AGENT` returns tool *names*, not marketing copy — but the *presence/absence* of a tool, which is the actual drift risk, is always live):

```python
TOOL_DISPLAY = {
    "Analyst": "Ask about machine health, anomalies, OEE, orders, inventory, and priority/RUL.",
    "explain_prediction": "Explain why a specific prediction was flagged (feature-level breakdown).",
    "create_jira_ticket": "File a maintenance ticket for a machine.",
    "request_jira_ticket": "Escalate/request maintenance attention on a machine.",
}
```

Chat page renders these under the caption as a short bullet list — this doubles as §6.3's greeting content.

### 6.3 Per-persona caption + greeting (user's choice: caption + short greeting line)

```python
st.caption(f"Chatting as {PERSONAS[persona]['label']} -- {PERSONAS[persona]['blurb']}")
for tool_name in get_agent_tool_names(AGENT_NAME):
    st.caption(f"• {TOOL_DISPLAY.get(tool_name, tool_name)}")
```

Replaces the current single static caption (lines 88-91 of the existing file).

---

## 7. Ticket-call confirmation UI (SH-52)

Module 7 §3 / SH-58 confirm the tool response shape: `{"status": "CREATED"|"ALREADY_OPEN"|"RECENTLY_CLOSED", "ticket_id": "SIM-<n>", "ticket_url": null}`. The Chat page's `_extract_text` (currently text-only, `pages/4_Chat.py` lines 34-39) is extended to also scan the response's tool-call/tool-result content items for `create_jira_ticket`/`request_jira_ticket` results and render a distinct confirmation **in addition to** the agent's own prose, so the presenter has an unambiguous visual signal independent of how the agent chose to word its text response:

```python
if tool_result.get("status") == "CREATED":
    st.success(f"Ticket {tool_result['ticket_id']} created.")
elif tool_result.get("status") == "ALREADY_OPEN":
    st.info(f"Machine already has an open ticket: {tool_result['ticket_id']}.")
elif tool_result.get("status") == "RECENTLY_CLOSED":
    st.info(f"A ticket for this machine was recently closed: {tool_result['ticket_id']}. Filing a new one may be worth reconsidering.")
```

**No clickable link is ever rendered** — `ticket_url` is always `null` per SH-58 §2's invariant; the confirmation shows `ticket_id` only, never attempts `st.markdown(f"[link]({ticket_url})")` or similar. This is invariant 2 for `Reviewer-agent` (§8).

**Flagged assumption (Developer-agent must verify live)**: the exact shape of tool-call/tool-result content items in the Agent `:run` JSON response (what `type` value marks a tool result, and where the procedure's returned JSON object lands within it) is not verifiable without a live session in this design conversation — same category of flagged risk the original Chat-page design doc (`SH-27-28-30-31-32-33`) called out for the SSE/response shape generally, and which that story resolved via live testing during build. This story's build must do the same for the tool-result shape specifically.

---

## 8. Invariants for Reviewer-agent

1. `SNOWCOMOTIVE_ROLE` is the only role referenced anywhere in this story's diff — no new `CREATE ROLE`/`GRANT` statements.
2. Ticket confirmation UI (§7) never renders `ticket_url` as a clickable link or any URL-shaped string — it is always `null` and must never be fabricated or implied.
3. `plant_manager_agent`'s `tools` list contains exactly one entry (`Analyst`) — no ticket tool, no `explain_prediction`.
4. Neither `production_planner_agent` nor the revised `maintenance_supervisor_agent` declares an `explain_prediction` tool (§1's exclusion — `SP_EXPLAIN_PREDICTION` doesn't exist).
5. `create_jira_ticket` (Supervisor) and `request_jira_ticket` (Planner) both resolve to `identifier: "SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET"` — same procedure, two tool names, per Module 7 §3's "same API, different framing" design.
6. Every page except `0_Choose_Persona.py` calls `require_persona()` and halts (via `st.stop()`) if no persona is set — direct URL navigation to any other page with no persona chosen must show the redirect prompt, not silently proceed with a default persona.
7. The Chat page's capability list (§6.2) is derived from a live `DESCRIBE AGENT` call against the actually-resolved `AGENT_NAME` for the active persona, not from a hardcoded persona→tool dict — confirms the "live introspection" decision was actually implemented, not simplified back to a static dict during build.
8. `scripts/07_post_setup.sql`'s semantic view `TABLES`/`FACTS`/`DIMENSIONS`/`METRICS`/`AI_VERIFIED_QUERIES` clauses are unchanged by this story's diff (§2 — already done) — only the `CREATE OR REPLACE AGENT` statements are added/modified.
9. Re-running `manage.py up` (or the post-setup step alone) after this story lands must not fail or duplicate objects — `CREATE OR REPLACE AGENT` idempotency holds for all 3 agents.

---

## 9. Explicitly flagged assumptions / open items for Developer-agent to verify live

1. **`DESCRIBE AGENT`'s exact output column/shape** (§6.2) — expected to include the full specification body (tools list included), matching how `DESC SEMANTIC VIEW`/`DESCRIBE AGENT` were already confirmed usable in the SH-27-28-30-31-32-33 review cycle, but the precise column name and whether it's YAML text vs. structured JSON was not re-verified in this design session. If it turns out `DESCRIBE AGENT` doesn't expose tool names in a parseable way, fall back to the static-dict approach the user explicitly declined here, and flag that reversal back to the user rather than silently reintroducing it.
2. **Tool-call/tool-result content-item shape in the Agent `:run` JSON response** (§7) — same category of previously-flagged-and-resolved risk from the original Chat page design; must be live-verified again for the ticket tool specifically, since no ticket tool existed when that page was first built and tested.
3. **`st.switch_page` path strings** (§5.3/§5.4) — must match the actual filenames Developer-agent ships (`pages/0_Choose_Persona.py`, `pages/1_Overview.py`); sketch above uses the filenames this doc specifies, adjust together if renamed.

---

## 10. Files to create/modify (Developer-agent's checklist)

**New**:
- `oee_command_center_app/pages/0_Choose_Persona.py` (§5)

**Modified**:
- `oee_command_center_app/streamlit_app.py` — add `PERSONAS` dict + `require_persona()` helper (§5.2/§5.4)
- `oee_command_center_app/pages/1_Overview.py`, `2_Prioritization.py`, `3_Forecast_OEE.py` — add one `require_persona()` call each, no other changes
- `oee_command_center_app/pages/4_Chat.py` — persona-resolved agent name (§6.1), live tool introspection + capability caption (§6.2/§6.3), ticket-result confirmation rendering (§7)
- `scripts/07_post_setup.sql` — add `CREATE OR REPLACE AGENT` for `production_planner_agent`/`plant_manager_agent`, revise `maintenance_supervisor_agent`'s in place to add `create_jira_ticket` (§4). Semantic view clauses untouched (§2).
- `scripts/README.md` — update row 07's description to mention 3 agents instead of 1, per convention.

**Reads only, no changes**: `docs/designs/SH-58-simulated-ticket-store.md` (procedure contracts), `docs/04-7-LLD.md` (tool specs — a future `Documenter-agent` footnote may follow, not this story).

---

## 11. Explicitly deferred

- SH-57 (Impact Statement page) — separate story, not touched.
- `explain_prediction` tool wiring for any agent — EPIC-STRETCH S-STRETCH-1, blocked on `SP_EXPLAIN_PREDICTION` not existing yet.
- `Documenter-agent` footnotes reconciling `docs/03-HLD.md` §5 and `docs/04-7-LLD.md` with this story's actual shipped instructions text — left for the reconciliation pass after build, per this repo's standard `Design-agent`→`Developer-agent`→`Reviewer-agent`→`Documenter-agent` flow.
- Renaming/renumbering existing pages — `0_Choose_Persona.py` slots in ahead of the existing `1_Overview.py` without renumbering anything else.

---

## 12. Live-verified results (`Reviewer-agent`, 2026-09-27)

Clean PASS on all 9 invariants (§8) — `SNOWCOMOTIVE_ROLE` is the only role touched by this diff (no new `CREATE ROLE`/`GRANT`), the ticket confirmation UI (§7) never renders `ticket_url` as a link or URL-shaped string (always `null`, matches SH-58's invariant exactly), `plant_manager_agent`'s `tools` list is exactly `[Analyst]` with no ticket tool and no `explain_prediction`, neither `production_planner_agent` nor the revised `maintenance_supervisor_agent` declares `explain_prediction` (§1's exclusion, `SP_EXPLAIN_PREDICTION` still doesn't exist), `create_jira_ticket`/`request_jira_ticket` both resolve to the same `SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET` identifier, every page except the picker calls `require_persona()` and halts via `st.stop()` on direct-URL navigation with no persona set, the Chat page's capability list is genuinely derived from a live `DESCRIBE AGENT` call (not a hardcoded fallback dict), `scripts/07_post_setup.sql`'s semantic-view clauses are untouched by this diff (§2's "already done" finding held), and re-running the post-setup step is idempotent for all 3 `CREATE OR REPLACE AGENT` statements. Verified both statically (reading the shipped code) and via independent live re-verification against `snow-co-cat-alyst-snowcomotive`, including a full live agent-run round trip that actually created a ticket end-to-end (not just a static-shape check).

**Both §9 flagged assumptions resolved during build — confirmed by Reviewer-agent as faithful re-implementations of the design's intent, not reversions to what the design explicitly declined:**

1. **`DESCRIBE AGENT`'s output shape (§9 item 1)**: `conn.query()` (Streamlit's Arrow-based Snowpark connector helper) cannot deserialize `DESCRIBE AGENT`'s result set — confirmed live, an Arrow-incompatibility error, not a transient issue. `Developer-agent` used `conn.cursor()` (bypassing Arrow, returning raw tuples/dicts) instead, then parsed the specification column exactly as §6.2 originally designed. This is not a fallback to the static-dict approach the user explicitly declined in §6.2/§9 — the live-introspection behavior (tool list can never drift from what's actually wired on the agent object) is fully intact; only the *connector method* used to fetch the row changed, for a documented Arrow-compatibility reason specific to `DESCRIBE AGENT`'s result shape.
2. **Tool-call/tool-result content-item shape in the Agent `:run` JSON response (§9 item 2)**: live-verified that `tool_result` payloads inside the SSE response are double-JSON-encoded — the outer content item's `content` field is itself a JSON string that must be `json.loads()`'d a second time before the `status`/`ticket_id`/`ticket_url` keys are reachable. `Developer-agent`'s implementation performs this second decode; §7's confirmation-rendering logic works against the fully-decoded dict exactly as designed, once that one extra decode step is applied.

**Live-verified ticket-creation scenario**: Reviewer-agent independently drove a full chat turn against `production_planner_agent` (via the live Streamlit app) explicitly asking it to escalate a specific machine, confirmed the agent called `request_jira_ticket` (not `create_jira_ticket` — persona-correct tool name), confirmed the Chat page rendered the `st.success("Ticket SIM-<n> created.")` confirmation with no `ticket_url` anywhere in the rendered output, and confirmed a corresponding new row landed in `snowcomotive.raw.jira_ticket` with `requested_by_persona = 'Production Planner'`.

**Files touched** (matches §10's file checklist exactly, no drift):
- New: `oee_command_center_app/pages/0_Choose_Persona.py` — persona picker landing page (§5).
- Modified: `oee_command_center_app/streamlit_app.py` — `PERSONAS` dict + `require_persona()` helper (§5.2/§5.4).
- Modified: `oee_command_center_app/pages/1_Overview.py`, `2_Prioritization.py`, `3_Forecast_OEE.py` — one `require_persona()` call added to each, no other changes.
- Modified: `oee_command_center_app/pages/4_Chat.py` — persona-resolved `AGENT_NAME` (§6.1), live `DESCRIBE AGENT` tool introspection + capability caption (§6.2/§6.3, via `conn.cursor()` per deviation 1 above), ticket-result confirmation rendering (§7, with the double-`json.loads()` per deviation 2 above).
- Modified: `scripts/07_post_setup.sql` — added `CREATE OR REPLACE AGENT` for `production_planner_agent`/`plant_manager_agent`, revised `maintenance_supervisor_agent` in place to add `create_jira_ticket` (§4). Semantic view clauses left untouched, per §2's finding.
- Modified: `scripts/README.md` — row 07's description updated to mention all 3 agents instead of 1.
