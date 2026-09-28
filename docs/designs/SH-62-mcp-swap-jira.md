# Design: SH-62 — MCP swap for Jira (S-STRETCH-2)

Status: **Design frozen** (brainstorm confirmed by user's explicit direction, given up front rather than interactively — see §2's attribution column; no further live Q&A needed before `Developer-agent` build, per user's instruction).
Branch: `feature/SH-8-62-mcp-swap-jira`
Epic: EPIC-JIRA | Story: SH-62 (S-STRETCH-2: "MCP swap for Jira")
Traces to: [docs/designs/SH-53-jira-sm-provision-auth.md](SH-53-jira-sm-provision-auth.md) (superseded real-Jira-REST auth path — this is what SH-62's Jira description means by "replace direct REST call with an MCP Jira connector"), [docs/designs/SH-58-simulated-ticket-store.md](SH-58-simulated-ticket-store.md) (the live simulated store this story adds alongside, not instead of), [docs/designs/SH-54-55-56-59-60-52-persona-suite.md](SH-54-55-56-59-60-52-persona-suite.md) (agent tool wiring + Chat page introspection/rendering this story extends), `scripts/07_post_setup.sql` (current 3-agent DDL)

**Jira description**: "Replace direct REST call with an MCP Jira connector — ticks the guidelines' explicit 'Connecting to additional sources via MCP' bullet."

**Explicitly NOT a replacement of SH-58**: per the user's direction (§2), the simulated ticket store (`snowcomotive.raw.jira_ticket` + its four `SP_*_JIRA_TICKET` procedures) is **not removed, not deprecated, not silently swapped**. This story *adds* a second, real-Jira-backed path alongside it. "Swap" in the story title refers to the auth *mechanism* (MCP-proxied OAuth vs. the permanently-blocked EAI/REST path from SH-53), not to removing the working simulated store.

---

## 1. Scope

Add a real-Jira-backed ticketing path via Snowflake's native `external_mcp` API integration type, pointed at Atlassian's hosted MCP server (`https://mcp.atlassian.com`), attached to the existing agents alongside their current simulated-store tools:

1. An `API INTEGRATION` (`API_PROVIDER = external_mcp`) authorizing Snowflake to reach `mcp.atlassian.com`.
2. An `EXTERNAL MCP SERVER` object wrapping that integration, referencing the Atlassian MCP endpoint.
3. A one-time, human-in-the-loop OAuth consent flow (`SYSTEM$START_USER_OAUTH_FLOW` / `SYSTEM$FINISH_OAUTH_FLOW`, or the Snowsight/CoWork connector-authorization UI) — required because `external_mcp` integrations only support `OAUTH_DYNAMIC_CLIENT`/`OAUTH2` auth, and Atlassian's hosted MCP server itself requires per-user consent to attribute ticket actions to a real Jira user. This **cannot** reuse SH-53's `jira_api_token` Secret — no static-token auth mode exists for this object type.
4. `mcp_servers:` attachment on `maintenance_supervisor_agent` and `production_planner_agent`, alongside their existing `create_jira_ticket`/`request_jira_ticket` custom tools (both paths coexist; `plant_manager_agent` gets neither, unchanged from its no-ticketing stance).
5. A `real_ticket_key` cross-reference column added to `snowcomotive.raw.jira_ticket` (nullable, populated only when a real MCP-created ticket exists for that row).
6. Chat page (`4_Chat.py`) updates: capability caption/tool-list rendering for the new MCP-backed tools, and ticket-confirmation rendering extended to recognize real Jira ticket keys (which, unlike `SIM-<n>`, DO get a real clickable `ticket_url`).
7. Explicit "read recent tickets for a machine" capability via the Atlassian MCP server's native tool surface (JQL-based), documented as a tool the agent can invoke once `mcp_servers:` is attached — exact tool name not independently confirmed live in this session (see §7, §9).

**Not in scope**: removing or deprecating the simulated store (§2 — explicit user direction); building any new stored procedure (the MCP path calls Atlassian's own hosted tools directly, no Snowflake-side procedure needed for create/read); resolving §9's open items without a live OAuth-authenticated session — those are explicitly deferred to manual verification.

---

## 2. Real-world decisions made during brainstorm

| Question | Decision | Attribution |
|---|---|---|
| Remove the simulated store once MCP works? | **No — never.** Simulated store keeps working exactly as today, permanently, not just during a transition window. | User's explicit instruction #1 (verbatim: "Do NOT remove the existing simulated ticket store capability... It must keep working exactly as it does today"). |
| Read-recent-tickets-for-a-machine capability? | **Add it**, via the MCP path specifically (not the local `SP_GET_JIRA_TICKET`, which only reads by exact `ticket_id`, not by `equipment_id`). | User's explicit instruction #2. |
| Simulated store's long-term fate (remove later vs. keep as audit trail)? | **Keep permanently as a cross-reference/audit log** — add `real_ticket_key` (nullable) to `snowcomotive.raw.jira_ticket`, populated only when a ticket is also filed via the new MCP path. Decided explicitly now, per the user's instruction to brainstorm and resolve this rather than leave it open. | User's explicit instruction #3, resolved per the user's own floated design (the `real_ticket_key`/`mcp_ticket_key` column idea). |
| Do both paths (simulated + MCP-real) coexist as agent tool options, with no silent swap of which persona gets which? | **Yes — coexist.** Both `create_jira_ticket`/`request_jira_ticket` (simulated, unchanged) AND the new `mcp_servers:` attachment ship on the same two agents in this same story. No persona/tool reassignment happens silently. | User's explicit instruction #4. |
| MCP object names/schema placement | `snowcomotive.cons` — matches where the agents themselves live (`maintenance_supervisor_agent`, `production_planner_agent`), not `raw` (unlike SH-53's now-superseded Secret/Network Rule, which lived in `raw` as an environment-scoped auth object). The MCP server object is agent-adjacent infrastructure, not a landing/raw-layer object. |
| API Integration name | `jira_mcp_integration` — account-level object (not schema-qualified, matching how `EXTERNAL ACCESS INTEGRATION` in SH-53 was also unqualified), parallel naming to SH-53's `jira_access_integration` but with `_mcp_` inserted to distinguish the two auth mechanisms unambiguously in `SHOW INTEGRATIONS` output. |
| MCP server name | `snowcomotive.cons.jira_mcp_server` — schema-qualified per `CREATE EXTERNAL MCP SERVER`'s object model (unlike the integration, this one lives inside a schema). |
| OAuth consent — where/when does it happen? | **New standalone `manage.py` command, `authorize-jira-mcp`**, modeled directly on SH-53's `setup-jira` precedent (a separate, rarely-re-run, human-present step — not folded into `manage.py up`). Unlike `setup-jira` (which reads a pre-generated token from the user), this command triggers `SYSTEM$START_USER_OAUTH_FLOW`, prints the authorization URL for the user to open in a browser, waits for confirmation, then calls `SYSTEM$FINISH_OAUTH_FLOW`. This is a genuinely new UX element with no exact precedent in this repo — flagged in §9 as needing live confirmation of the exact `SYSTEM$*` function signatures/return shapes, which were not exercised end-to-end in this session's feasibility check (only the DDL objects and agent attachment were verified, not a completed OAuth round-trip). |
| Attach MCP server to agents directly via `mcp_servers:`, or a separate mechanism? | **Directly via `mcp_servers:` in the agent spec**, alongside the existing `tools:`/`tool_resources:` blocks — this is exactly what this session's live feasibility test confirmed works (`CREATE AGENT ... FROM SPECIFICATION $$ ... mcp_servers: - server_spec: name: "..." $$`, confirmed present in `DESCRIBE AGENT`'s `agent_spec` JSON). No separate wiring mechanism exists or is needed. |
| Which agents get `mcp_servers:` attached? | `maintenance_supervisor_agent` and `production_planner_agent` — same two agents that already have the simulated-store ticket tool, for direct create/escalate + read-recent-tickets parity. `plant_manager_agent` gets neither the simulated tool nor the MCP server — unchanged, still a strictly read-only-via-Analyst persona (its own instructions text already tells users to redirect elsewhere for ticketing; that framing is correct for both paths and needs no edit). |
| "Read recent tickets for a machine" — what tool, what inputs? | The Atlassian hosted MCP server's own native JQL-search tool (its `tools/list` surface was not independently inspected live in this session — see §7 for the documented, not-yet-live-confirmed contract, and §9 item 2 for what must be manually verified before Reviewer-agent can sign off on this specific capability). Documented here as calling a `searchJiraIssuesUsingJql`-shaped tool with a JQL string built from `equipment_id` (e.g. `project = SUP AND text ~ "<equipment_id>" ORDER BY created DESC`), NOT a new Snowflake-side procedure — the MCP server exposes this natively, so no `SP_*` object is created for it. |
| Does `SP_CREATE_JIRA_TICKET`'s dedupe logic get extended to consider MCP-created tickets? | **No — explicitly not, for this story.** The dedupe predicate (open/recently-closed check) stays scoped to the local `jira_ticket` table exactly as SH-58 shipped it. Cross-checking against real Jira via a live MCP call before every simulated-ticket creation would add real latency/failure-mode coupling between the two paths that the user did not ask for and that isn't needed for this story's scope (both paths are independent create paths, not a single funnel). Flagged as an explicit non-goal, not an oversight (§10). |
| `real_ticket_key` — set by whom, when? | Populated by the agent-orchestration flow: when a user asks the agent to create a ticket via the MCP path (a distinct explicit ask from "file a ticket" — see §6 for exact instruction-text framing distinguishing the two), and the agent also has context of a corresponding local simulated ticket (or creates one as a cross-reference row), the Chat page — not a stored procedure — issues a follow-up call to `SP_LINK_MCP_TICKET` (new, §5.2) to stamp the real key onto the matching local row. This keeps the write path auditable (a real DML statement, visible in query history) without requiring the MCP-side tool itself to know about Snowflake's local table. |
| `ticket_url` for real MCP tickets — still always `null`? | **No — this is the key behavioral difference from the simulated store.** A real Jira ticket does have a real, valid URL (`https://chirajpepz.atlassian.net/browse/<KEY>`), and the MCP tool's response is expected to include one. SH-58's invariant ("`ticket_url` always null") applies **only** to the simulated-store procedures' own response shape — it is not project-wide policy for every ticketing path. The Chat page's ticket-confirmation rendering must distinguish the two: simulated results still never render a link; real MCP-ticket results may render a real clickable link. This is called out explicitly because it is easy to over-apply SH-58's invariant here by copy-paste habit. |

---

## 3. New SQL objects — `snowcomotive.cons` (agent-adjacent schema, per §2)

```sql
-- ============================================================================
-- MCP Jira integration (SH-62, S-STRETCH-2)
-- Traces to: docs/designs/SH-53-jira-sm-provision-auth.md (superseded auth
-- path), docs/designs/SH-58-simulated-ticket-store.md (coexisting, not
-- replaced), this design doc §2/§3.
--
-- Live-verified this session (feasibility only, then cleaned up -- see
-- header of this design doc's originating conversation): all three DDL
-- statements below succeeded as ACCOUNTADMIN on this account
-- (`<account-identifier>`), confirmed via
-- DESCRIBE/SHOW before being dropped. NOT yet re-run as part of a
-- committed setup script at doc-freeze time -- Developer-agent re-creates
-- these for real as part of this story's build.
-- ============================================================================

CREATE API INTEGRATION IF NOT EXISTS jira_mcp_integration
  API_PROVIDER = external_mcp
  API_ALLOWED_PREFIXES = ('https://mcp.atlassian.com')
  API_USER_AUTHENTICATION = (TYPE = OAUTH_DYNAMIC_CLIENT, OAUTH_RESOURCE_URL = 'https://mcp.atlassian.com/v1/mcp')
  ENABLED = TRUE;

CREATE EXTERNAL MCP SERVER IF NOT EXISTS snowcomotive.cons.jira_mcp_server
  WITH DISPLAY_NAME = 'Jira MCP (Atlassian hosted)'
  URL = 'https://mcp.atlassian.com/v1/mcp'
  API_INTEGRATION = jira_mcp_integration;

GRANT USAGE ON INTEGRATION jira_mcp_integration TO ROLE snowcomotive_role;
```

**`IF NOT EXISTS`, not `OR REPLACE`** — matches SH-53's stance on the Secret (§7 invariant 2 of that doc): once a human has completed the OAuth consent flow against a given integration/server pair, re-running setup must not force them through consent again. This differs from `07_post_setup.sql`'s existing convention of `CREATE OR REPLACE AGENT` for the agent objects themselves (those have no attached credential state to lose on replace) — the MCP integration/server pair is the one new object type in this story that behaves like SH-53's Secret, not like an agent or the semantic view.

**Placement rationale (§2)**: `snowcomotive.cons` for the MCP server (schema-qualified object, lives beside the agents that reference it); the API integration is account-level and unqualified, same pattern as SH-53's `jira_access_integration`.

---

## 4. OAuth consent — `manage.py authorize-jira-mcp` (new standalone command)

Modeled directly on SH-53's `setup-jira` precedent (`docs/designs/SH-53-jira-sm-provision-auth.md` §4): a separate, rarely-re-run, human-present step, never folded into `manage.py up`/`down`.

```python
@app.command("authorize-jira-mcp")
def authorize_jira_mcp() -> None:
    """One-time human-in-the-loop OAuth consent for the Jira MCP server
    (SH-62). Unlike setup-jira (SH-53), there is no token to type -- this
    opens a browser-based Atlassian consent screen. Must be re-run if the
    OAuth grant is ever revoked or expires without a refresh path."""
    run_authorize_jira_mcp()
```

```python
def run_authorize_jira_mcp() -> None:
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT SYSTEM$START_USER_OAUTH_FLOW('jira_mcp_integration')"
        )
        auth_url = cur.fetchone()[0]
        print(f"Open this URL and complete Atlassian's consent screen:\n{auth_url}")
        typer.confirm("Press Enter once you've completed the consent flow in your browser", default=True)
        cur.execute(
            "SELECT SYSTEM$FINISH_OAUTH_FLOW('jira_mcp_integration')"
        )
        print(cur.fetchone()[0])
    finally:
        conn.close()
```

**Flagged, not live-confirmed** (§9 item 1): the exact `SYSTEM$START_USER_OAUTH_FLOW`/`SYSTEM$FINISH_OAUTH_FLOW` function signatures, argument shape (integration name as a bare string vs. some other identifier form), and return-value shape (a bare URL string vs. a JSON/variant object) were **not** exercised end-to-end in this session's feasibility check — only the three DDL statements in §3 were confirmed live. `Developer-agent` must live-test this exact call sequence and adjust the sketch above to match actual behavior; this is a documented best-guess based on the general `SYSTEM$*_USER_OAUTH_FLOW` family's public naming convention, not a verified transcript.

**Why a new command, not reusing `setup-jira`**: different credential shape entirely (browser consent vs. a pasted token) and a different target object type (`API INTEGRATION`/`EXTERNAL MCP SERVER` vs. `SECRET`/`NETWORK RULE`/`EXTERNAL ACCESS INTEGRATION`) — conflating them into one command would make `setup-jira`'s existing token-prompt UX confusing for a flow that has no token at all.

---

## 5. Local table cross-reference column + link procedure

### 5.1 `snowcomotive.raw.jira_ticket` — add `real_ticket_key`

```sql
ALTER TABLE snowcomotive.raw.jira_ticket
  ADD COLUMN IF NOT EXISTS real_ticket_key STRING;
```

- Nullable, no default — `NULL` for every row created before this story, and for every row where no corresponding real MCP ticket was ever filed. This is the common case; most simulated tickets will never get a real counterpart.
- Populated only via §5.2's procedure, never inline inside `SP_CREATE_JIRA_TICKET` itself (that procedure's contract, §4.1 of SH-58's frozen doc, is unchanged by this story — no new parameter added to it).
- **Not part of the dedupe predicate** — per §2's explicit decision, `SP_CREATE_JIRA_TICKET`'s existing OPEN/RECENTLY_CLOSED check (SH-58 §4.1) does not look at `real_ticket_key` and is not extended to query the MCP server before creating a simulated ticket.

### 5.2 `SP_LINK_MCP_TICKET(ticket_id, real_ticket_key)` — new, `snowcomotive.cons`

```sql
CREATE OR REPLACE PROCEDURE snowcomotive.cons.SP_LINK_MCP_TICKET(
  ticket_id STRING,
  real_ticket_key STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.10'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
AS
$$
def run(session, ticket_id, real_ticket_key):
    updated = session.sql(
        """
        UPDATE snowcomotive.raw.jira_ticket
        SET real_ticket_key = ?
        WHERE ticket_id = ?
        """,
        params=[real_ticket_key, ticket_id],
    ).collect()
    # UPDATE via session.sql().collect() returns a summary row, not affected rows directly --
    # Developer-agent to confirm exact result-row shape for the "found" check live.
    found = True  # placeholder pending live confirmation, see note above
    return {"found": found, "ticket_id": ticket_id, "real_ticket_key": real_ticket_key}
$$;
```

- A **fifth** procedure alongside SH-58's four `SP_*_JIRA_TICKET` procedures — kept separate rather than folded into `SP_UPDATE_JIRA_TICKET_STATUS`, matching this project's existing "split by capability" rationale (SH-58 §1): linking a real ticket key is a conceptually distinct write from opening/closing a ticket's status.
- Called by the Chat page (§6), not by an agent tool directly — this is app-orchestration glue, not something the agent itself decides to invoke as a `tool_spec`. No new `tool_spec`/`tool_resources` entry is added to any agent's YAML for this procedure.
- **Flagged for Developer-agent** (§9 item 3): the exact row-count/affected-rows shape returned by `UPDATE ... .collect()` in a Snowpark Python stored procedure was not live-verified in this design session; the `found` determination sketched above is a placeholder — must be confirmed against a real "row exists" vs. "row missing" test before this ships.

---

## 6. Agent spec changes — `mcp_servers:` attachment

`maintenance_supervisor_agent` and `production_planner_agent` each gain an `mcp_servers:` top-level key, alongside their existing `tools:`/`tool_resources:` (both unchanged from `docs/designs/SH-54-55-56-59-60-52-persona-suite.md` §4.1/§4.2 — no removal of `create_jira_ticket`/`request_jira_ticket`).

```yaml
# Added to maintenance_supervisor_agent's FROM SPECIFICATION body, alongside
# the existing tools:/tool_resources: blocks (unchanged, per §2's coexistence
# decision):
mcp_servers:
  - server_spec:
      name: "snowcomotive.cons.jira_mcp_server"
```

Instruction-text addition (both agents), distinguishing the two paths explicitly so the agent never conflates them:

```
  orchestration: >
    ...(existing text unchanged)...
    You now also have access to a real Jira connector (via the Jira MCP
    server) for two additional capabilities: creating a REAL Jira ticket
    (distinct from the simulated create_jira_ticket/request_jira_ticket
    tool -- only use the real Jira connector when the user explicitly asks
    for a "real" ticket, filed "in Jira itself," or similar unambiguous
    language; default to the simulated tool otherwise, since it is
    instant and does not require the user to have completed a one-time
    browser authorization), and reading recent tickets already filed for
    a specific machine (use this whenever the user asks what tickets
    exist or have recently been filed for a given equipment_id -- this is
    a read-only lookup against real Jira, safe to call proactively when
    asked). Never conflate a simulated ticket ID (format SIM-<n>) with a
    real Jira ticket key -- they are visually distinguishable and must
    never be presented as interchangeable.
```

**`plant_manager_agent` — unchanged.** No `mcp_servers:` block added. Its existing instruction text ("You have no ticketing tool... redirect the user to the Production Planner or Maintenance Supervisor persona instead") already covers both ticketing paths correctly without any edit, since it's phrased as "no ticketing tool" generically rather than naming the specific simulated tool.

**Why default-to-simulated in the instruction text**: per §2's decision that both paths coexist without a silent swap, the agent needs an explicit disambiguation rule so it doesn't start preferring the newer, flashier MCP path by default — the simulated path remains the zero-friction default (no browser step, no live external dependency) unless the user is explicit about wanting a real ticket.

---

## 7. "Read recent tickets for a machine" — tool contract (documented, not live-confirmed)

**What this is**: a read-only lookup, exposed natively by Atlassian's hosted MCP server once `mcp_servers:` is attached (§6) — no new Snowflake-side procedure or agent `tool_spec` entry is created for this; the agent calls the MCP server's own tool directly, the same way it calls `Analyst` for semantic-view questions.

**Documented expected contract** (best-available understanding at doc-freeze time, per this story's instructions, since the Atlassian MCP server's `tools/list` surface was not independently inspected live in this session — only integration/server/agent-attachment plumbing was verified, §9 item 2):

- Tool name: expected to be JQL-search-shaped, most likely something resembling `searchJiraIssuesUsingJql` (the same tool name convention already visible in this IDE's own Atlassian MCP integration, `mcp_atlassian_searchJiraIssuesUsingJql`) — Atlassian's hosted `mcp.atlassian.com` server is a different deployment than this IDE's own bundled Atlassian MCP tools, so the exact name is not guaranteed to match; `Developer-agent` must confirm the real name via a live `tools/list` inspection (through Snowsight's agent tool-testing UI or a live `agent:run` call) once OAuth consent (§4) is complete.
- Expected inputs: a JQL query string. The agent is instructed (§6) to construct one filtering by `equipment_id` — e.g. `project = SUP AND (summary ~ "<equipment_id>" OR text ~ "<equipment_id>") ORDER BY created DESC` — assuming ticket summaries/descriptions created via SH-53's original intended field mapping (Module 9 §1) would have included the equipment ID as searchable text. This assumption itself is unverified until a real ticket has actually been created via the MCP path with real field content to search against.
- Expected outputs: a list of issues with at minimum key, summary, status, and created timestamp — sufficient for the agent to summarize "here are the N most recent tickets for this machine" in prose, and (per §2's `ticket_url` decision) each result is expected to carry a real, renderable browse URL.

**This is the single largest unverified surface in this story** — flagged explicitly in §9 as requiring a live, OAuth-authenticated session to confirm the actual tool name, input schema, and output shape before `Reviewer-agent` can sign off on this capability specifically (as distinct from the DDL/attachment plumbing, which is separately confirmed per §"Live-verified feasibility findings" below).

---

## 8. Chat page changes (`oee_command_center_app/pages/4_Chat.py`)

### 8.1 Capability caption / tool introspection (§6.2 of the persona-suite doc, extended)

`get_agent_tool_names()`'s existing `DESCRIBE AGENT` parsing (which reads `spec.get("tools", [])` and extracts `tool_spec.name`) does **not** currently look at a spec's `mcp_servers:` key at all — MCP-attached capabilities would silently not appear in the capability caption today. This story adds a second extraction path:

```python
def get_agent_mcp_server_names(agent_name: str) -> list[str]:
    """Companion to get_agent_tool_names() -- MCP servers are a separate
    top-level key in DESCRIBE AGENT's agent_spec JSON (mcp_servers, not
    tools), so they need their own extraction, or the capability caption
    silently omits any MCP-backed capability entirely."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(f"DESCRIBE AGENT {AGENT_DATABASE}.{AGENT_SCHEMA}.{agent_name}")
        row = cur.fetchone()
        columns = [d[0] for d in cur.description]
    finally:
        cur.close()
    spec = json.loads(row[columns.index("agent_spec")])
    return [server["server_spec"]["name"] for server in spec.get("mcp_servers", [])]
```

`TOOL_DISPLAY` gets new entries for MCP-backed capability display copy (the display-name lookup pattern is unchanged, just extended):

```python
MCP_SERVER_DISPLAY = {
    "snowcomotive.cons.jira_mcp_server": "Create a real Jira ticket, or look up recent tickets for a machine, via the live Jira connector.",
}
```

Caption rendering loop (§6.3 of the persona-suite doc) gets a second `for` loop appended, over `get_agent_mcp_server_names(agent_name)`, same `st.caption(f"• ...")` pattern.

**Flagged assumption, not live-confirmed** (§9 item 4): whether `DESCRIBE AGENT`'s `agent_spec` JSON key for MCP attachments is literally `mcp_servers` (matching the YAML key used in `FROM SPECIFICATION`) was confirmed live in this session's feasibility test at DDL-creation time ("confirmed present in `DESCRIBE AGENT`'s `agent_spec` JSON" per the task's stated findings) — so this specific piece is **not** an open item, unlike §7's tool-contract question. Included here as a confirmed-working detail, not a flagged risk.

### 8.2 Ticket-confirmation rendering — distinguish simulated vs. real

`render_ticket_confirmation()` and `_extract_ticket_results()` currently only understand the simulated store's tool-result shape (`create_jira_ticket`/`request_jira_ticket`, `TICKET_TOOL_NAMES`). This story does **not** need to make these two functions parse the MCP server's tool-result shape defensively from scratch, because the exact shape of a real Jira MCP tool-result content item is itself unverified (§9 item 2 — same category of risk as §7). Instead:

- `TICKET_TOOL_NAMES` stays scoped to the two existing simulated-tool names — unchanged.
- A **new, separate** rendering path is added for MCP tool results, gated by tool name matching the real MCP tool name once confirmed (§7/§9) — e.g. a new `MCP_TICKET_TOOL_NAMES` set and a parallel `_extract_mcp_ticket_results()`/`render_mcp_ticket_confirmation()` pair, structurally mirroring the existing functions but rendering a **real clickable link** (`st.markdown(f"[View in Jira]({ticket_url})")`) since, per §2's decision, real MCP tickets do carry a real, safe-to-render URL.
- These two rendering paths (simulated vs. MCP-real) are kept as visually and structurally distinct code paths — not merged into one `render_ticket_confirmation()` with an `if is_simulated` branch — because their `ticket_url` handling is opposite (simulated: never render a link; real: always render a link when present), and conflating them risks a copy-paste bug where SH-58's "never render `ticket_url`" invariant gets silently over-applied to real tickets, or the reverse (a real key with a fabricated/guessed URL being rendered when the tool didn't actually return one).

**Flagged assumption** (§9 item 5): the exact content-item shape for a tool result originating from an `mcp_servers:`-attached server (as opposed to a `generic`-type custom tool like `create_jira_ticket`) in the Agent `:run` JSON response was not live-verified in this session — this is a structurally different tool type than anything previously wired into these agents, so the existing double-`json.loads()` pattern documented in `4_Chat.py`'s `_extract_ticket_results()` docstring (discovered during the persona-suite story) is not guaranteed to apply unchanged; `Developer-agent` must independently confirm this shape live.

---

## 9. Items explicitly requiring manual OAuth-flow / live-session verification before Reviewer-agent sign-off

The following were **not** and **could not** be live-verified during this design session's feasibility check, because they require a completed, per-user OAuth consent grant against a real Atlassian account — something this session's ad hoc feasibility test deliberately did not attempt (only DDL object creation, agent attachment, and `DESCRIBE AGENT` output were confirmed, then the objects were dropped). These are **manual verification steps for the user and/or `Reviewer-agent`**, not silently assumed working:

1. **`SYSTEM$START_USER_OAUTH_FLOW`/`SYSTEM$FINISH_OAUTH_FLOW` exact signatures and return shapes** (§4) — `manage.py authorize-jira-mcp`'s implementation is a best-guess sketch based on the general naming convention; must be run for real, once, by the user completing the actual browser consent screen, before this command can be considered working.
2. **The Atlassian hosted MCP server's actual `tools/list` surface** (§7) — the exact tool name, input schema, and output shape for the "search/read tickets" capability is undocumented-from-live-testing at doc-freeze time; requires an authenticated session to inspect (e.g. via Snowsight's agent tool-testing UI, or a live `agent:run` call after §4's consent flow completes).
3. **Actual ticket creation through the MCP path end-to-end** — creating a real ticket, confirming it lands in the real `SUP` project (or wherever Atlassian's hosted connector defaults to / is configured to target), confirming the response shape matches what §8.2's rendering logic expects, and confirming `SP_LINK_MCP_TICKET` (§5.2) correctly stamps `real_ticket_key` onto the right local row.
4. **`SP_LINK_MCP_TICKET`'s row-found detection** (§5.2) — the placeholder `found = True` in the sketch must be replaced with a real check against `UPDATE`'s actual affected-row-count/result shape in a Snowpark Python procedure.
5. **MCP-originated tool-result content-item shape in the Agent `:run` JSON response** (§8.2 item 5) — cannot be confirmed without a completed create-or-read call actually reaching Atlassian's live server.

**What CAN and already WAS live-verified** (cited as fact per this story's instructions, not re-verified in this design session, but not requiring further manual sign-off either):
- `CREATE API INTEGRATION ... API_PROVIDER = external_mcp ...` succeeds on this account.
- `CREATE EXTERNAL MCP SERVER ...` succeeds, referencing that integration.
- `CREATE AGENT ... FROM SPECIFICATION $$ ... mcp_servers: - server_spec: name: "..." $$` succeeds, and the `mcp_servers` key is confirmed present and correctly shaped in `DESCRIBE AGENT`'s `agent_spec` JSON output.

---

## 10. Invariants for Reviewer-agent

1. `snowcomotive.raw.jira_ticket`'s four existing procedures (`SP_CREATE_JIRA_TICKET`, `SP_GET_JIRA_TICKET`, `SP_UPDATE_JIRA_TICKET_STATUS`, `SP_DELETE_JIRA_TICKET`) are **completely unmodified in behavior** — same signatures, same dedupe predicate, same `ticket_url: null` contract for every one of their responses. The only change to the table itself is the additive `real_ticket_key` column (§5.1).
2. `SP_CREATE_JIRA_TICKET`'s dedupe check is **not** extended to query the MCP server or consider `real_ticket_key` — confirmed as an explicit non-goal (§2), not an oversight if absent.
3. `create_jira_ticket` (on `maintenance_supervisor_agent`) and `request_jira_ticket` (on `production_planner_agent`) remain present, unmodified, in both agents' `tools:`/`tool_resources:` blocks — `mcp_servers:` is purely additive, never a replacement of either tool.
4. `plant_manager_agent`'s spec gains no `mcp_servers:` block and no new tool — its `tools:` list remains exactly `[Analyst]`.
5. `jira_mcp_integration` and `snowcomotive.cons.jira_mcp_server` both use `IF NOT EXISTS` (not `OR REPLACE`) — re-running setup after a human has completed OAuth consent must not force re-consent.
6. Chat page's simulated-ticket rendering path (`render_ticket_confirmation`/`_extract_ticket_results`/`TICKET_TOOL_NAMES`) is unmodified and still never renders `ticket_url` as a link for `SIM-<n>`-style results — the new MCP-real rendering path (§8.2) is additive and structurally separate, not a branch inside the existing functions.
7. The MCP-real ticket-confirmation path DOES render a real clickable link when the underlying tool result includes one — this is the deliberate behavioral asymmetry from SH-58's invariant, confirmed correct per §2's decision, not a regression of "never fabricate a URL."
8. `get_agent_tool_names()` (existing, unmodified) and the new `get_agent_mcp_server_names()` (§8.1) are two separate functions/extraction paths, both parsing live `DESCRIBE AGENT` output — no hardcoded persona→MCP-capability dict is introduced, matching the persona-suite story's original "live introspection, never a static dict" decision.
9. No change is made to `plant_manager_agent`'s instruction text — its existing "no ticketing tool" framing already covers both paths correctly without an edit.
10. §9's five manual-verification items are explicitly called out in the PR description / commit message as unverified-pending-user-OAuth-completion — `Reviewer-agent` must not mark this story as a full end-to-end pass based on static code review alone; a note referring back to this doc's §9 is required in the review report, and full sign-off on items 2/3/5 is blocked until the user has personally completed `manage.py authorize-jira-mcp` and reports back.

---

## 11. File checklist (for `Developer-agent`)

**New**:
- A setup-script addition (new numbered script, e.g. `07c_setup_jira_mcp.sql`, following the `07`/`07b` precedent for standalone-not-in-`up` scripts) creating `jira_mcp_integration`, `snowcomotive.cons.jira_mcp_server` (§3), and `snowcomotive.cons.SP_LINK_MCP_TICKET` (§5.2).
- `manage.py` — new `authorize-jira-mcp` command + `run_authorize_jira_mcp()` (§4), standalone, not part of `up`/`down`.

**Modified**:
- `snowcomotive.raw.jira_ticket` — `ALTER TABLE ... ADD COLUMN IF NOT EXISTS real_ticket_key STRING` (§5.1), via the new setup script (not retroactively edited into SH-58's original `07b_setup_jira_ticket_store.sql` — additive DDL belongs in this story's own script per the "revise the right file, don't touch unrelated history" convention already used elsewhere in this repo, e.g. SH-58 §8's stance on SH-53's files).
- `scripts/07_post_setup.sql` — `maintenance_supervisor_agent` and `production_planner_agent`'s `FROM SPECIFICATION` bodies gain `mcp_servers:` block + instruction-text addition (§6). `production_planner_agent`/`plant_manager_agent`'s existing blocks otherwise unchanged. `plant_manager_agent` untouched entirely.
- `scripts/README.md` — new row for the setup script above, following the `07`/`07b`/`08b` row conventions.
- `oee_command_center_app/pages/4_Chat.py` — `get_agent_mcp_server_names()` (§8.1), `MCP_SERVER_DISPLAY` (§8.1), new MCP-real ticket-confirmation extraction/rendering pair (§8.2), capability caption loop extended.

**Reads only, no changes**: `docs/designs/SH-53-jira-sm-provision-auth.md`, `docs/designs/SH-58-simulated-ticket-store.md` (both stay as historical record — no amendment section added to either by this story; a future `Documenter-agent` pass may add cross-referencing footnotes, not done here).

---

## 12. Explicitly deferred / open items

- All five items in §9 — blocked on a human completing a real OAuth consent flow; cannot be resolved by static design or code review alone.
- Whether `manage.py authorize-jira-mcp` needs a corresponding "revoke"/"deauthorize" command — not asked for, not built, symmetric with SH-53's stance on token rotation (a deliberate separate action, not built preemptively).
- Whether the real MCP-ticket-creation path should also get its own `SP_*`-style Snowflake wrapper procedure for consistency with the simulated store's shape — explicitly not needed for this story (the MCP server's native tool is called directly by the agent, no Snowflake-side procedure required for create/read against Atlassian's hosted server) — flagged only in case a future story wants symmetry for its own sake, not because anything here is incomplete without it.
- A `Documenter-agent` footnote reconciling `docs/04-9-LLD.md` §3 (auth mechanism) with this story's addition of a third auth path (MCP OAuth, alongside the original REST+Secret design and the simulated-store's "no auth needed" pivot) — left for the reconciliation pass after build.
- Teardown (`09_teardown.sql`) handling for `jira_mcp_integration`/`jira_mcp_server` — per this project's existing stance on external credentials (SH-53 §8's precedent: not dropped by teardown), these should also survive teardown; not built into `09_teardown.sql` in this story since that script itself is still "Not built" project-wide (per `scripts/README.md` row 10).

---

## 13. Summary of what this story does NOT change (v1, pre-amendment — see §14 for what supersedes this)

- The simulated ticket store — table, sequence, all four procedures — is untouched in behavior (§10 invariant 1).
- `plant_manager_agent` — completely untouched, no new tool, no new instruction text.
- SH-53's own Secret/Network Rule/EAI objects — not touched, not reused (confirmed impossible per the Jira-issue-provided auth-constraint finding), not removed either — remain exactly as SH-58 §8 left them (a historical, unused-but-harmless artifact).

---

## 14. AMENDMENT (2026-09-28): retire the simulated store, drop the scripted OAuth CLI, resolve role-scoping, add dedupe + list-incidents orchestration

**Status: this amendment is itself frozen** (brainstorm confirmed by the user's explicit up-front direction, same "give direction, don't re-litigate" pattern as §2's original brainstorm — no further live Q&A before `Developer-agent` build).

**Why this amendment exists, and why §1–§13 above are not deleted**: the real MCP-based Jira ticket creation described above has now been live-verified end-to-end by the user, but only through Snowsight's/Snowflake CoWork's own "Connect" UI — the `manage.py authorize-jira-mcp` scripted OAuth round-trip (§4/§9 item 1) was attempted many times this session and never completed successfully (`"Invalid request to complete OAuth flow"` / `"OAuth state parameter is not present"` regardless of encoding, timing, same-session handling, or role). With the real path now proven to work via the UI, the user has decided to (1) stop trying to script OAuth entirely, (2) remove the simulated store now that a working real path exists, (3) accept the platform's actual constraint on role-scoped MCP access rather than build a tier that isn't possible, and (4)/(5) extend the Supervisor's orchestration instructions for list-incidents and best-effort dedupe. Per this project's "revise, don't replace" convention (`docs/05-Epics.md` §2, also followed by `07_post_setup.sql`'s own versioning), this is a new dated section reconciling/superseding §1–§13, not a new file — the original sections remain as the historical record of how the MCP swap was first designed and fed into the earlier round of Reviewer-agent live verification (§9/§12 of this doc's original content).

### 14.1 Real-world decisions made during this amendment's brainstorm

| # | Question | Decision | Attribution |
|---|---|---|---|
| 1 | Keep the simulated ticket store now that real MCP tickets work? | **No — remove entirely.** Drop `create_jira_ticket`/`request_jira_ticket` tool_spec/tool_resources from both agents; drop `SP_CREATE_JIRA_TICKET`, `SP_GET_JIRA_TICKET`, `SP_UPDATE_JIRA_TICKET_STATUS`, `SP_DELETE_JIRA_TICKET`, `SP_LINK_MCP_TICKET`, the `jira_ticket` table, and the `jira_ticket_id_seq` sequence — reverses §2's original "never remove" decision now that the real path is proven live. | User's explicit instruction #1, this session. |
| 2 | DROP the now-dead objects, or leave them dormant/unreferenced? | **DROP**, via a new standalone teardown script (§14.3). Justification: (a) `AGENTS.md`'s own stated project value is "never let \[docs and reality\] silently drift" — a `jira_ticket` table sitting in `raw` with a `real_ticket_key` column that no longer means anything once the simulated path is gone is exactly that kind of drift; (b) unlike SH-53's Secret/Network Rule (kept dormant per SH-58 §8 because they were *never successfully exploitable* on this account tier, so dropping them would have no functional benefit over leaving them inert), these five procedures and the table are fully working, callable objects that would keep silently succeeding if left in place — a stale, undocumented capability is worse than a documented absence; (c) `SP_LINK_MCP_TICKET` in particular has zero purpose without the simulated store to cross-reference (§5.2's whole raison d'être), so leaving it dormant is leaving genuinely dead code, not preserved-for-later infrastructure. | Resolved per user's instruction #1 ("Decide explicitly... pick one and justify it"). |
| 3 | Keep trying to complete OAuth from `manage.py`? | **No.** Shrink `authorize-jira-mcp` to a read-only existence/enabled check + printed pointer to the Snowsight Agent UI / Snowflake CoWork Connectors UI, which both worked cleanly on the first real attempt. No more `SYSTEM$START_USER_OAUTH_FLOW`/`SYSTEM$FINISH_OAUTH_FLOW` calls anywhere in `manage.py`. | User's explicit instruction #2. |
| 4 | Give `production_planner_agent` a reduced/read-only Jira tier instead of nothing? | **Rejected — not possible on this platform.** `EXTERNAL MCP SERVER` (Atlassian-hosted, `API_PROVIDER = external_mcp`) exposes whatever tool set the provider publishes as a whole; Snowflake has no mechanism to grant an agent a subset of an external MCP server's tools (unlike `CREATE MCP SERVER FROM SPECIFICATION`, which does support cherry-picking specific UDF/procedure/search/analyst tools for a *Snowflake-managed* server). Since §1 also removes the simulated tool, Planner ends up with **zero** Jira capability, real or simulated — escalation happens purely by the human switching personas to Supervisor in the app UI. | User's explicit instruction #3, confirmed as a genuine platform constraint (not a design preference) via this session's own analysis of `external_mcp` vs. `CREATE MCP SERVER FROM SPECIFICATION`'s documented capabilities. |
| 5 | Support "list all open incidents" with no machine named? | **Yes — add it** to `maintenance_supervisor_agent`'s orchestration instructions: same MCP search tool as the existing per-equipment_id lookup, just without an `equipment_id` filter in the constructed query when the user doesn't name a specific machine. | User's explicit instruction #4. |
| 6 | Dedupe before creating a real ticket? | **Yes, but prompt-enforced only, not a hard guarantee.** No Snowflake-side hook exists to wrap or gate Atlassian's own create-issue MCP tool, so the only lever is orchestration instructions telling the agent to search before create. Explicitly documented as best-effort — the LLM could still fail to sequence the two calls correctly, unlike the simulated store's deterministic SQL predicate (SH-58 §4.1), which this replaces in spirit but not in reliability. | User's explicit instruction #5. |

### 14.2 Platform-constraint deep dive (for §14.1 row 4 — documented explicitly per the user's ask, not silently omitted)

`CREATE EXTERNAL MCP SERVER` wraps a third-party-hosted MCP endpoint (here, `mcp.atlassian.com`) behind one `API INTEGRATION`. Once an agent's `mcp_servers:` block references that server object, the agent gets access to **every tool the remote server's own `tools/list` response advertises** — Snowflake has no `tool_resources`-style allow-list or per-tool grant surface for `external_mcp` servers, unlike:
- Custom `generic` tools (`create_jira_ticket`, `request_jira_ticket`) — individually declared per agent, individually removable, which is exactly how §1's removal and the original persona-suite split (SH-56) worked.
- `CREATE MCP SERVER FROM SPECIFICATION` (the *Snowflake-managed* variant, wrapping the customer's own UDFs/procedures/search services/semantic views) — this variant lets the creator explicitly enumerate which underlying tools the MCP server exposes at creation time, so a subset could in principle be built. Atlassian's server is not this variant; it's a third-party-hosted `external_mcp` endpoint, so this option does not apply here.

**Resolution**: no "read-only Planner" tier is built. `production_planner_agent` gets no `mcp_servers:` key at all — the binary choice this platform actually offers is "full MCP server tool access" or "none," and giving Planner the full server (including real create-ticket) would violate the persona's "plan, don't dispatch" framing from the original persona-suite design (`docs/designs/SH-54-55-56-59-60-52-persona-suite.md` §4.2), so "none" is the only choice consistent with that framing.

### 14.3 Simulated-store removal — objects and teardown script

**Objects being dropped from live Snowflake** (all currently live per `07b_setup_jira_ticket_store.sql` and `07c_setup_jira_mcp.sql`'s `SP_LINK_MCP_TICKET`/`real_ticket_key` addition):
- `snowcomotive.cons.sp_create_jira_ticket(STRING, FLOAT, FLOAT, STRING, STRING)`
- `snowcomotive.cons.sp_get_jira_ticket(STRING)`
- `snowcomotive.cons.sp_update_jira_ticket_status(STRING, STRING)`
- `snowcomotive.cons.sp_delete_jira_ticket(STRING)`
- `snowcomotive.cons.sp_link_mcp_ticket(STRING, STRING)`
- `snowcomotive.raw.jira_ticket` (table)
- `snowcomotive.raw.jira_ticket_id_seq` (sequence)

**New standalone script, `scripts/07d_retire_simulated_ticket_store.sql`** (follows the `07`/`07b`/`07c` non-`up` precedent — human-run once, not wired into `manage.py up`/`down`/`post-setup`):

```sql
-- ============================================================================
-- 07d_retire_simulated_ticket_store.sql — retires the simulated Jira ticket
-- store and its MCP cross-reference plumbing (SH-62 amendment, 2026-09-28).
-- Traces to: docs/designs/SH-62-mcp-swap-jira.md §14.1 (decision row 1/2),
-- docs/designs/SH-58-simulated-ticket-store.md (the store being retired).
--
-- Run ONCE, standalone, human-present -- not part of manage.py up/down/
-- post-setup. Safe to re-run (every statement is IF EXISTS).
--
-- Drop order matters only cosmetically here (no FK-style dependency between
-- these objects), but procedures are dropped before the table/sequence they
-- read/write, for readability.
-- ============================================================================

USE ROLE snowcomotive_role;

DROP PROCEDURE IF EXISTS snowcomotive.cons.sp_link_mcp_ticket(STRING, STRING);
DROP PROCEDURE IF EXISTS snowcomotive.cons.sp_delete_jira_ticket(STRING);
DROP PROCEDURE IF EXISTS snowcomotive.cons.sp_update_jira_ticket_status(STRING, STRING);
DROP PROCEDURE IF EXISTS snowcomotive.cons.sp_get_jira_ticket(STRING);
DROP PROCEDURE IF EXISTS snowcomotive.cons.sp_create_jira_ticket(STRING, FLOAT, FLOAT, STRING, STRING);
DROP TABLE IF EXISTS snowcomotive.raw.jira_ticket;
DROP SEQUENCE IF EXISTS snowcomotive.raw.jira_ticket_id_seq;
```

**`scripts/07c_setup_jira_mcp.sql` — revised in place**: remove the `real_ticket_key` `ALTER TABLE` statement and the entire `SP_LINK_MCP_TICKET` procedure (both existed only to cross-reference simulated tickets, per the now-superseded §5 above). The file shrinks to just the `CREATE API INTEGRATION` / `GRANT USAGE` / `CREATE EXTERNAL MCP SERVER` statements (§3 of this doc's original content) — no other change to that script.

**`scripts/07b_setup_jira_ticket_store.sql` — header amended, file NOT deleted**: add a `RETIRED (SH-62 amendment, 2026-09-28) — see docs/designs/SH-62-mcp-swap-jira.md §14 and scripts/07d_retire_simulated_ticket_store.sql` note at the top of the file's existing header comment. The file is left in place as historical record of the original design (same "preserve history, don't delete" convention as SH-58 §8's treatment of SH-53's now-unused files) — it is simply never run again, and its objects no longer exist live once `07d` has been run.

**`scripts/README.md`** — row `08b`'s Status cell gets a `RETIRED (SH-62 amendment)` prefix; a new row `08d` documents `07d_retire_simulated_ticket_store.sql`; row `08c`'s description drops the `real_ticket_key`/`SP_LINK_MCP_TICKET` mentions to match the shrunk script.

### 14.4 `manage.py authorize-jira-mcp` — new read-only-check behavior

**Removed entirely**: `SYSTEM$START_USER_OAUTH_FLOW`, `SYSTEM$FINISH_OAUTH_FLOW`, the `.jira_mcp_redirect_url.txt` file-based redirect-URL handoff, the `typer.confirm`/`input()` interactive wait — none of this proved reliable across many live attempts this session, while the Snowsight Agent UI's own "MCP Connectors → Connect" flow and Snowflake CoWork's "Connectors → Connect" flow both worked cleanly on the first real attempt.

**New `run_authorize_jira_mcp()`** — a read-only existence/enabled check, then a printed pointer to the UI flow that actually works:

```python
def run_authorize_jira_mcp() -> None:
    """SH-62 amendment (2026-09-28): read-only existence/status check only --
    no OAuth flow is scripted from here anymore. SYSTEM$START_USER_OAUTH_FLOW/
    SYSTEM$FINISH_OAUTH_FLOW proved unreliable from a CLI/script context
    across many live attempts this session ("Invalid request to complete
    OAuth flow" / "OAuth state parameter is not present" regardless of
    encoding, timing, same-session handling, or role), while Snowsight's own
    Agent UI and Snowflake CoWork's Connectors UI both worked cleanly on the
    first real attempt. This command now only confirms the
    jira_mcp_integration/jira_mcp_server objects exist and are enabled, then
    prints instructions pointing the human at the UI flow."""
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        cur.execute("SHOW INTEGRATIONS LIKE 'jira_mcp_integration'")
        integration_rows = cur.fetchall()
        cur.execute("SHOW EXTERNAL MCP SERVERS LIKE 'jira_mcp_server' IN SCHEMA snowcomotive.cons")
        server_rows = cur.fetchall()
    finally:
        conn.close()

    if not integration_rows:
        print("jira_mcp_integration not found -- run scripts/07c_setup_jira_mcp.sql first.")
        return
    if not server_rows:
        print("snowcomotive.cons.jira_mcp_server not found -- run scripts/07c_setup_jira_mcp.sql first.")
        return

    print("jira_mcp_integration and snowcomotive.cons.jira_mcp_server both exist.\n")
    print("Scripted OAuth (SYSTEM$START_USER_OAUTH_FLOW/SYSTEM$FINISH_OAUTH_FLOW) was")
    print("abandoned after repeated live failures from a CLI/script context. Authorize")
    print("(or re-authorize) this connector via one of these UIs instead:\n")
    print("  Option A -- Snowsight Agent UI:")
    print("    AI & ML -> Agents -> select maintenance_supervisor_agent -> MCP Connectors tab -> Connect\n")
    print("  Option B -- Snowflake CoWork:")
    print("    Open Snowflake CoWork -> Connectors/Sources panel -> Connect")
    print("    (this is the flow already live-verified working, this session)")
```

**Flagged, not live-confirmed**: the exact `SHOW EXTERNAL MCP SERVERS` syntax (object-type keyword, whether `IN SCHEMA` is accepted the same way as `SHOW INTEGRATIONS`) was not re-verified in this amendment session — `Developer-agent` must confirm live and adjust if the actual command differs (e.g. `SHOW MCP SERVERS`, or no `LIKE`/`IN SCHEMA` support). This is a strictly smaller/safer surface than the abandoned OAuth calls (read-only `SHOW`, no state mutation), so a syntax miss here fails loudly and safely rather than silently.

**`manage.py`'s module docstring and the `authorize-jira-mcp` Typer command's own docstring** are both updated to match — no more "single continuous run" / "same session" language (that constraint applied only to the scripted `SYSTEM$*` pair, which no longer exists in this command).

### 14.5 Final agent-spec end-state — all 3 agents

| Agent | `tools:` | `mcp_servers:` |
|---|---|---|
| `maintenance_supervisor_agent` | `[Analyst]` only | `[snowcomotive.cons.jira_mcp_server]` |
| `production_planner_agent` | `[Analyst]` only | *(key omitted entirely)* |
| `plant_manager_agent` | `[Analyst]` only | *(unchanged — never had one)* |

#### 14.5.1 `maintenance_supervisor_agent` — revised `instructions.orchestration` (replaces the §6/original-build text in full; `instructions.response` and the `Analyst` tool/tool_resources block are unchanged)

```yaml
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, inventory,
    priority score, or predicted remaining-useful-life (RUL). Explaining
    *why* a specific prediction was made (a feature-level breakdown) is not
    enabled yet; if asked, say so explicitly rather than guessing at a
    feature-level rationale -- reporting a predicted value returned by
    Analyst is fine and expected, that is not the same capability.
    You have access to a real Jira connector (via the Jira MCP server) for
    three capabilities: creating a real Jira ticket, listing open tickets
    for a specific machine, and listing all open tickets regardless of
    machine. Only create a ticket when the user explicitly asks to file,
    create, or dispatch a maintenance ticket for a specific machine --
    never proactively, even if a machine looks at-risk. ALWAYS create
    tickets in the SUP project (project key "SUP", the Service Management/
    Support project on chirajpepz.atlassian.net) -- never in SH or any
    other project, even if the connector's default/most-recently-used
    project suggestion is different; explicitly pass or select project key
    SUP when creating the issue. Before creating a ticket for a given
    machine, you MUST first search/list open tickets for that machine's
    equipment_id using the connector's search tool -- if an open ticket
    already exists for that machine, report it to the user instead of
    creating a duplicate; only create a new ticket if no open ticket is
    found for it. When the user asks what open incidents or tickets exist
    without naming a specific machine, use the same search tool with an
    unfiltered query (all open tickets in project SUP) rather than telling
    them you can only look up tickets for a named machine. Report exactly
    what the connector returns -- ticket key, status, summary -- never
    fabricate or imply a ticket exists that the tool did not actually
    return.
```

`tools:`/`tool_resources:` block: drop the entire `create_jira_ticket` `tool_spec` entry and its `tool_resources.create_jira_ticket` entry — only the `Analyst` tool_spec/tool_resources remains. `mcp_servers:` block is unchanged (`snowcomotive.cons.jira_mcp_server`).

#### 14.5.2 `production_planner_agent` — revised `instructions.orchestration` (replaces the §6/original-build text in full)

```yaml
  orchestration: >
    Use the Analyst tool for questions about demand/orders, finished-goods
    and spare-parts inventory, OEE, and priority/RUL signals -- frame
    answers around production-planning impact (will we meet demand, what
    is the OEE or supply risk) rather than raw sensor detail. You have no
    ticketing tool and no Jira connector of any kind -- never suggest
    filing, creating, or escalating a maintenance ticket yourself; if
    asked, say that ticketing is not available for this persona and
    redirect the user to the Maintenance Supervisor persona instead.
```

`tools:`/`tool_resources:` block: drop the entire `request_jira_ticket` `tool_spec` entry and its `tool_resources.request_jira_ticket` entry — only the `Analyst` tool_spec/tool_resources remains (identical shape to `plant_manager_agent`'s single-tool block). `mcp_servers:` top-level key is removed entirely from this agent's `FROM SPECIFICATION` body.

#### 14.5.3 `plant_manager_agent` — unchanged

No edit. Its existing "You have no ticketing tool... redirect the user to the Production Planner or Maintenance Supervisor persona instead" framing already covers the post-amendment world correctly (it was already phrased generically, not naming a specific tool) — this is the same observation the original §6 made about this agent needing no edit, still true here.

### 14.6 Chat page (`oee_command_center_app/pages/4_Chat.py`) — strip the simulated-ticket rendering path

**Removed entirely**:
- `TOOL_DISPLAY`'s `"create_jira_ticket"` and `"request_jira_ticket"` entries (the `"Analyst"`/`"explain_prediction"` entries stay — `explain_prediction` remains a documented-not-yet-built placeholder, unrelated to this amendment).
- `TICKET_TOOL_NAMES` module-level set.
- `_extract_ticket_results()` function.
- `render_ticket_confirmation()` function.
- The two call sites in the chat-history replay loop and the live-turn handler that call `_extract_ticket_results`/`render_ticket_confirmation` and store `ticket_results` on a chat-history message dict — `ticket_results` key is dropped from the message dict shape entirely.

**Kept, unchanged**: `MCP_SERVER_DISPLAY`, `get_agent_tool_names()`, `get_agent_mcp_server_names()`, `MCP_TICKET_TOOL_NAMES`, `_extract_mcp_ticket_results()`, `render_mcp_ticket_confirmation()`, and their call sites — all of this is the real-MCP-path rendering machinery, untouched by removing the simulated path. Since `production_planner_agent` no longer has `mcp_servers:` attached (§14.5.2), `get_agent_mcp_server_names()` will simply return an empty list for that persona — no code change needed there, it already handles "agent has no MCP servers" correctly by construction (`spec.get("mcp_servers", [])`).

### 14.7 Invariants for Reviewer-agent (this amendment — supersedes §10's invariants for anything they conflict with; §10 invariants 6/8/9 about `plant_manager_agent` and idempotency still hold unchanged)

1. `create_jira_ticket` and `request_jira_ticket` do not appear anywhere in `scripts/07_post_setup.sql` (grep confirms zero matches for both strings) — neither as a `tool_spec` name nor a `tool_resources` key.
2. `maintenance_supervisor_agent`'s DDL `tools:` list contains exactly one entry (`Analyst`); its `mcp_servers:` block is present and still references `snowcomotive.cons.jira_mcp_server`.
3. `production_planner_agent`'s DDL `tools:` list contains exactly one entry (`Analyst`); its `FROM SPECIFICATION` body contains no `mcp_servers:` key at all.
4. `plant_manager_agent`'s DDL block is byte-for-byte unchanged by this amendment's diff.
5. Live `DESCRIBE AGENT` on `production_planner_agent` shows no `mcp_servers` key in the `agent_spec` JSON, and no tool named `create_jira_ticket`/`request_jira_ticket` in its `tools` list.
6. Live `DESCRIBE AGENT` on `maintenance_supervisor_agent` shows `tools` = `["Analyst"]` and `mcp_servers` containing `snowcomotive.cons.jira_mcp_server`.
7. `maintenance_supervisor_agent`'s orchestration instructions text contains an explicit search-before-create dedupe directive (grep for a distinguishing phrase such as "search/list open tickets... before creating a new ticket").
8. `maintenance_supervisor_agent`'s orchestration instructions text contains an explicit unfiltered/all-open-tickets directive for when no machine is named (grep for a distinguishing phrase such as "without naming a specific machine").
9. None of `SP_CREATE_JIRA_TICKET`, `SP_GET_JIRA_TICKET`, `SP_UPDATE_JIRA_TICKET_STATUS`, `SP_DELETE_JIRA_TICKET`, `SP_LINK_MCP_TICKET` exist live in `snowcomotive.cons` after `07d_retire_simulated_ticket_store.sql` has been run (`SHOW PROCEDURES LIKE '%JIRA_TICKET%' IN SCHEMA snowcomotive.cons` returns zero rows).
10. `snowcomotive.raw.jira_ticket` and `snowcomotive.raw.jira_ticket_id_seq` do not exist live after the same script has been run (`SHOW TABLES`/`SHOW SEQUENCES LIKE 'JIRA_TICKET%' IN SCHEMA snowcomotive.raw` return zero rows).
11. `scripts/07c_setup_jira_mcp.sql` contains no reference to `sp_link_mcp_ticket` or `real_ticket_key` after this amendment's edit.
12. `oee_command_center_app/pages/4_Chat.py` contains no reference to `render_ticket_confirmation`, `_extract_ticket_results`, `TICKET_TOOL_NAMES`, `create_jira_ticket`, or `request_jira_ticket` (grep confirms zero matches) — the MCP-real rendering path (`render_mcp_ticket_confirmation`, `_extract_mcp_ticket_results`, `MCP_TICKET_TOOL_NAMES`, `get_agent_mcp_server_names`) is untouched.
13. `manage.py`'s `run_authorize_jira_mcp()` contains no call to `SYSTEM$START_USER_OAUTH_FLOW` or `SYSTEM$FINISH_OAUTH_FLOW`, and no file-based redirect-URL handoff (`.jira_mcp_redirect_url.txt` is not referenced).
14. Re-running `scripts/07_post_setup.sql` (via `manage.py post-setup`) after this amendment is idempotent for all 3 `CREATE OR REPLACE AGENT` statements — same idempotency stance as the original §10 invariant 9, re-confirmed because the DDL bodies changed materially.

### 14.8 Items requiring manual, human-present verification (supersedes/narrows §9 — several §9 items are now moot because the capability they gated has been proven working via the UI; the remaining live unknowns are narrower)

**No longer open** (resolved by the user's own live UI-based verification this session, cited as fact, not re-verified in this design session): real ticket creation via the MCP path works end-to-end through Snowsight's/CoWork's own "Connect" UI, landing a real ticket in a real Jira project. §9's original items 1 and 3 (OAuth signatures, end-to-end creation) are resolved to "OAuth via UI works; scripted OAuth is abandoned, not fixed" — not further open items for `manage.py` specifically, since `manage.py` no longer attempts OAuth at all (§14.4).

**Still open, require a human to exercise the live app after this amendment ships**:
1. **Dedupe behavior** (§14.1 row 6) — ask the Supervisor persona to file a ticket for the same machine twice in a row; confirm it searches first and reports the existing open ticket the second time rather than creating a duplicate. Explicitly best-effort/prompt-enforced — a failure here is a known limitation to note, not necessarily a build defect, unless it fails to even attempt the search step at all.
2. **Unfiltered list-open-incidents** (§14.1 row 5) — ask "what open incidents do we have?" with no machine named; confirm the agent calls the search tool without an `equipment_id` filter and returns a real list of open `SUP` tickets rather than asking the user to name a machine first.
3. **The Atlassian hosted MCP server's actual `tools/list` surface** (carried over from the original §9 item 2/§7 — still not independently inspected live in any design session) — now more consequential than before, since Supervisor's instructions (§14.5.1) depend on the agent correctly distinguishing a "create" tool from a "search"/"list" tool by whatever names the server actually exposes, without this doc hardcoding either name.
4. **`production_planner_agent` has zero ticketing capability, confirmed in the live app** — open the Chat page as the Planner persona, confirm the capability caption shows no ticketing/MCP line at all, and confirm asking it to escalate or file a ticket produces the redirect-to-Supervisor response (§14.5.2's instruction text) rather than any tool call.
5. **`SHOW EXTERNAL MCP SERVERS` syntax** (§14.4) — confirm the exact `SHOW`-family command `run_authorize_jira_mcp()` should use; adjust if the live syntax differs from this doc's best-guess.

### 14.9 File checklist (this amendment)

**New**:
- `scripts/07d_retire_simulated_ticket_store.sql` (§14.3).

**Modified**:
- `scripts/07_post_setup.sql` — `maintenance_supervisor_agent` and `production_planner_agent`'s `FROM SPECIFICATION` bodies revised per §14.5.1/§14.5.2; `plant_manager_agent` untouched.
- `scripts/07c_setup_jira_mcp.sql` — remove the `real_ticket_key` `ALTER TABLE` statement and the `SP_LINK_MCP_TICKET` procedure (§14.3).
- `scripts/07b_setup_jira_ticket_store.sql` — header amended with a `RETIRED` note (§14.3); file not deleted.
- `scripts/README.md` — row `08b` marked retired, new row `08d` added, row `08c`'s description trimmed (§14.3).
- `manage.py` — `run_authorize_jira_mcp()` rewritten to the read-only check (§14.4); module docstring and the `authorize-jira-mcp` Typer command's docstring updated to match.
- `oee_command_center_app/pages/4_Chat.py` — simulated-ticket rendering path removed (§14.6); MCP-real rendering path untouched.

**Reads only, no changes**: `docs/designs/SH-58-simulated-ticket-store.md`, `docs/designs/SH-53-jira-sm-provision-auth.md`, `docs/designs/SH-54-55-56-59-60-52-persona-suite.md` — all three stay as historical record; a future `Documenter-agent` pass may add cross-referencing footnotes noting this amendment, not done here.

### 14.10 Explicitly deferred / open items (this amendment)

- All 5 items in §14.8 — blocked on a human exercising the live app/UI; cannot be resolved by static design or code review alone.
- A `Documenter-agent` footnote reconciling `docs/04-7-LLD.md`/`docs/04-9-LLD.md` with the simulated store's full removal — left for the reconciliation pass after build, same convention as every prior story in this repo.
- Whether `scripts/09_teardown.sql` (still "Not built" project-wide, per `scripts/README.md` row 10) should eventually fold in `07d`'s drops — not decided here; `07d` stands alone as its own standalone script for now, matching the `07`/`07b`/`07c` precedent of not being part of `up`/`down`.

### 14.11 Summary of what this amendment does NOT change

- `plant_manager_agent` — untouched, same as the original §13 stated.
- The MCP-real ticket-confirmation rendering path in `4_Chat.py` (`render_mcp_ticket_confirmation`, `_extract_mcp_ticket_results`, `MCP_TICKET_TOOL_NAMES`, `MCP_SERVER_DISPLAY`, `get_agent_mcp_server_names`) — unmodified.
- `jira_mcp_integration` / `snowcomotive.cons.jira_mcp_server` themselves (the DDL objects, §3 of the original content) — unmodified; only the *OAuth-consent tooling* around them (`manage.py authorize-jira-mcp`) and the *simulated-store cross-reference plumbing* (`real_ticket_key`, `SP_LINK_MCP_TICKET`) change.
- SH-53's own Secret/Network Rule/EAI objects — still untouched, still not reused, still not removed, per the original §13's stance (unaffected by this amendment).
