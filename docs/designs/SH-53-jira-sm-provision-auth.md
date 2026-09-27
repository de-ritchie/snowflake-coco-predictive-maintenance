# Design: SH-53 — Provision SM project + auth (S-JIRA-1)

Status: **Design frozen** (brainstorm confirmed by user) → `Developer-agent` build (no deviations from this doc) → **`Reviewer-agent` clean PASS (2026-09-27), all 7 invariants held** (see §10). `Documenter-agent` reconciliation complete.
Branch: `feature/SH-8-53-jira-sm-provision-auth`
Epic: EPIC-JIRA | Story: SH-53 (S-JIRA-1: "Provision SM project + auth")
Traces to: [docs/04-9-LLD.md](../04-9-LLD.md) §3 ("Auth mechanism"), [docs/05-Epics.md](../05-Epics.md) §1 ("Two Jira projects, do not conflate") and §8 (EPIC-JIRA table), [docs/04-10-LLD.md](../04-10-LLD.md) §1/§8 (setup script conventions, idempotency), `scripts/01_setup.sql` (existing setup-script convention this story follows), `scripts/README.md`, `manage.py` (lifecycle-script invocation pattern).

**Explicitly not this story's job**: `SP_CREATE_JIRA_TICKET`'s procedure body, field mapping, or duplicate-check JQL logic — that's SH-58/S-JIRA-2, a separate story that consumes the Secret/EAI this story provisions. This story is provisioning only.

---

## 1. Scope

Provision the Snowflake-side auth plumbing so a future stored procedure can call Jira's REST API v3 directly from inside Snowflake, per Module 9 §3:

1. A Snowflake `SECRET` holding a real Jira Cloud API token.
2. A `NETWORK RULE` allowing egress to the Jira Cloud host.
3. An `EXTERNAL ACCESS INTEGRATION` tying the two together, ready for `SP_CREATE_JIRA_TICKET` (SH-58) to declare `EXTERNAL_ACCESS_INTEGRATIONS = (jira_access_integration)` / `SECRETS = ('jira_token' = jira_api_token)` against.

**Not in scope**: the stored procedure itself, Jira field mapping/custom fields (`RUL Hours`, `Requested By` — SH-58's job once it actually builds the ticket-creation call), ticket-creation tool wiring into any agent (SH-60/S-JIRA-3), duplicate-check JQL logic.

---

## 2. Real-world decisions made during brainstorm

| Question | Decision |
|---|---|
| Which Jira site/project is the SM target (A5)? | **Reuse the existing `SUP` project** (`chirajpepz.atlassian.net`, key `SUP`, name "Support", `project_type = service_desk`) — confirmed live via `mcp_atlassian_getVisibleJiraProjects` during this brainstorm. No new project is created. (User's first instinct was a new project with key `SUP`, but that key is already taken by this same project — reusing it turned out to be the actual intent once that conflict surfaced.) |
| Does provisioning need to *create* a Jira project? | No — this eliminates Module 10 §1's original pseudocode (`jira_client.create_project(...)`) entirely for this story. See §5 for what "verify" means now that no creation is needed. |
| API token vs. MCP OAuth? | A real Jira Cloud API token is required and cannot be substituted with the Atlassian MCP OAuth connection. The MCP OAuth session is scoped to *this IDE conversation* — it lets tools in this session call Jira APIs. A Snowflake stored procedure has no access to that session; it can only reach the outside world via a Snowflake External Access Integration, which authenticates its own outbound call using a static credential stored in a `SECRET` object. These are two unrelated auth mechanisms serving two unrelated call paths (IDE-session tooling vs. a running stored procedure) — this is also why `AGENTS.md`'s Jira-project split (A10 Kanban via MCP vs. A5 SM via direct REST+Secret) exists in the first place. |
| Does the user have a token? | Yes, already generated. Not committed anywhere — supplied at build/run time only (see §4). |
| How does the token reach `CREATE SECRET` without being committed? | A SQL session variable, set via a bind parameter from Python at runtime (see §4) — never written into the `.sql` file itself. |
| File location | New, separate script: `scripts/01b_setup_jira.sql` — kept apart from `01_setup.sql`'s role/warehouse/db bootstrap since it has a fundamentally different re-run cadence (needs a secret value supplied by hand) and a different trust boundary (external credential vs. pure internal DDL). |

---

## 3. SQL — `scripts/01b_setup_jira.sql`

New script, run as `snowcomotive_role` (same role as everything past step 1 of `manage.py up`, per `scripts/README.md`'s run-order table — this is not a role/warehouse-bootstrap step, so it doesn't need `ACCOUNTADMIN`).

```sql
-- ============================================================================
-- 01b_setup_jira.sql — Jira SM auth provisioning (S-JIRA-1, SH-53)
-- Traces to: FR-OPS-01, docs/04-9-LLD.md §3, docs/05-Epics.md EPIC-JIRA
-- Jira: SH-53
--
-- Provisions ONLY the Secret / Network Rule / External Access Integration a
-- future SP_CREATE_JIRA_TICKET (SH-58) will declare against. Does NOT create
-- a Jira project -- the target SM project (A5) is the existing `SUP` project
-- on chirajpepz.atlassian.net, confirmed to already exist (see this story's
-- design doc, docs/designs/SH-53-jira-sm-provision-auth.md §2/§5).
--
-- Run separately from 01_setup.sql (not folded into `manage.py up`) because
-- it requires a real Jira Cloud API token supplied at invocation time --
-- see manage.py's `setup-jira` command. Re-running this script without a
-- token is safe (CREATE SECRET IF NOT EXISTS is a no-op if the secret
-- already exists); the Network Rule / External Access Integration below
-- contain no secret material and always use CREATE OR REPLACE.
--
-- Usage: uv run python manage.py setup-jira   (prompts for/reads the token,
-- never accepts it as a bare CLI arg -- see manage.py docstring)
-- ============================================================================

USE ROLE snowcomotive_role;

-- --- Secret: Jira Cloud API token ---
-- IF NOT EXISTS (not OR REPLACE): once provisioned, re-running this script
-- (e.g. as part of a broader idempotency check) must not require the token
-- again, and must not silently invalidate a working credential.
CREATE SECRET IF NOT EXISTS snowcomotive.raw.jira_api_token
  TYPE = GENERIC_STRING
  SECRET_STRING = $jira_api_token;   -- bound via SQL session variable, set by manage.py at runtime -- never hardcoded here

-- --- Network Rule: egress to Jira Cloud ---
CREATE OR REPLACE NETWORK RULE snowcomotive.raw.jira_network_rule
  TYPE = HOST_PORT
  MODE = EGRESS
  VALUE_LIST = ('chirajpepz.atlassian.net:443');

-- --- External Access Integration ---
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION jira_access_integration
  ALLOWED_NETWORK_RULES = (snowcomotive.raw.jira_network_rule)
  ALLOWED_AUTHENTICATION_SECRETS = (snowcomotive.raw.jira_api_token)
  ENABLED = TRUE;

GRANT USAGE ON INTEGRATION jira_access_integration TO ROLE snowcomotive_role;
GRANT READ ON SECRET snowcomotive.raw.jira_api_token TO ROLE snowcomotive_role;
```

**Object placement**: Secret and Network Rule live in `snowcomotive.raw` (consistent with the rest of this project's schema-per-layer convention — there's no dedicated "ops"/"admin" schema, and `raw` is where other environment-scoped, non-model objects like the landing stage already live per `01_setup.sql`). `EXTERNAL ACCESS INTEGRATION` is account-level by nature (Snowflake doesn't scope EAIs to a schema), so it isn't schema-qualified.

**Why `EAI`/network rule are always `CREATE OR REPLACE` but the Secret is `IF NOT EXISTS`**: the first two are pure DDL with no embedded secret material — replacing them is always safe and cheap. The Secret is the one object where "replace" would mean "requires the token again on every single re-run of this script," which defeats the point of making this script separately, rarely re-run. If the token ever needs to be rotated, that's a deliberate `ALTER SECRET ... SET SECRET_STRING = ...` or a manual `DROP SECRET` + re-run — not a side effect of routine idempotency testing.

---

## 4. Token passing — `manage.py setup-jira` (new command)

New `typer` command, modeled on the existing `up`/`down`/`demo` commands' connection pattern (`snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")`):

```python
@app.command("setup-jira")
def setup_jira(
    token: str = typer.Option(
        None, "--token", envvar="JIRA_API_TOKEN",
        help="Jira Cloud API token. Prefer the JIRA_API_TOKEN env var over this flag "
             "(a bare CLI arg is visible in shell history/process listings).",
    ),
) -> None:
    """Provision the Jira SM Secret/Network Rule/External Access Integration (SH-53)."""
    if not token:
        token = typer.prompt("Jira Cloud API token", hide_input=True)
    run_setup_jira(token)
```

```python
def run_setup_jira(token: str) -> None:
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        cur.execute("SET jira_api_token = %s", (token,))
        run_sql_file(cur, SCRIPTS_DIR / "01b_setup_jira.sql")
    finally:
        conn.close()
```

**Why a `SET` session variable, not a Python-side string-substitution into the SQL text**: string-substituting the token into the `.sql` file's text before executing it risks the token ending up in `run_sql_file`'s own `print(f"  {stmt.splitlines()[0][:80]}...")` debug line or any future logging of full statement text. A bound `SET` variable keeps the actual token value out of any string that gets constructed, printed, or written to disk — the executed statement text itself only ever contains the literal `$jira_api_token` reference, never the value.

**Why a new top-level `manage.py` command, not folded into `up`**: `up`/`down` must always be runnable end-to-end without a human present (e.g. from a fresh environment) — requiring a Jira token on every `up` would break that, and per §2's decision, this is a rarely-re-run, separate-trust-boundary step. `manage.py`'s own docstring convention (numbered stages) gets a short new stage note pointing at this command; it's explicitly *not* inserted into the numbered `up` sequence.

**Precedence**: `--token` flag > `JIRA_API_TOKEN` env var > interactive `hide_input` prompt. The flag is included only because `typer.Option(envvar=...)` needs it declared for the env var to work — the help text actively steers away from using it directly on the command line.

---

## 5. "Verify" the SM project — documented, not a live runtime check

Module 10 §1's original pseudocode (`if not jira_client.project_exists(...): jira_client.create_project(...)`) assumed project creation might be needed. Since §2 confirms `SUP` already exists and is being reused, there is nothing to create. Building a *live* existence check into `manage.py`/this script would require adding a Jira REST client + a second credential path into a Python script whose only other job is Snowflake DDL — a real dependency-and-auth-surface increase to check something already confirmed once, live, during this design session (§2's table).

**Decision**: no runtime "does SUP exist" check is added to `01b_setup_jira.sql` or `manage.py`. The script's own header comment (§3) states the assumption explicitly (`SUP` already exists) so a future reader isn't left wondering why no creation logic exists. If `SUP` is ever deleted or renamed, `SP_CREATE_JIRA_TICKET` (SH-58) will fail loudly at its first live API call against project `SUP` — an acceptable failure mode per this project's own FR-OPS-06 stance ("safe to invoke out of order" means "doesn't corrupt state," not "silently succeeds regardless of environment drift").

This is flagged as an open item (§8) rather than silently decided, since it's a real scope-narrowing versus the LLD's original pseudocode.

---

## 6. `scripts/README.md` update

Add a new row to the lifecycle-script table:

| # | File | Stage | Status | Jira |
|---|---|---|---|---|
| 01b | `01b_setup_jira.sql` | Jira SM auth provisioning (Secret/Network Rule/EAI) — run via `manage.py setup-jira`, separate from `up` | Built (v1 — provisioning only, no ticket-creation procedure yet) | SH-53 (S-JIRA-1) |

And a short note in the "Run order" prose: `01b` is **not** part of the `01 → 02 → ... → 09` sequence — it's invoked standalone, once per environment (or once per token rotation), whenever a human is present to supply the token.

---

## 7. Invariants for Reviewer-agent

1. The Jira Cloud API token value must never appear in a committed file, in `manage.py`'s own printed/logged output, or in `01b_setup_jira.sql`'s literal text — only `$jira_api_token` (the session-variable reference) may appear in the SQL file.
2. `CREATE SECRET` uses `IF NOT EXISTS` (not `OR REPLACE`) — re-running `01b_setup_jira.sql` against an environment that already has the secret must not require a token to succeed at the Network Rule/EAI statements below it. (Verify by running the script twice — the second run should not depend on the SET-variable value being fresh/valid, though the SET statement itself will still require *some* value to be bound by `run_setup_jira`; the invariant is specifically about `CREATE SECRET` not clobbering an existing value, not about `manage.py setup-jira` becoming token-optional.)
3. `NETWORK RULE`'s `VALUE_LIST` is exactly `('chirajpepz.atlassian.net:443')` — matches the hostname confirmed in §2, not a placeholder.
4. No Jira project-creation call (REST or otherwise) is added anywhere in this story's changes — per §5's explicit decision, `SUP` is assumed to already exist, not created or verified live.
5. No `SP_CREATE_JIRA_TICKET` procedure, field mapping, or duplicate-check logic is added — this story's scope is provisioning only (§1).
6. `01b_setup_jira.sql` is a genuinely separate file, not appended into `01_setup.sql` — matches §2's file-location decision.
7. `manage.py setup-jira` is not called anywhere inside `run_up()`/`run_down()` — it must remain a standalone command per §4's rationale.

---

## 8. Explicitly deferred / open items

- **SM project existence is a documented assumption, not a live-checked invariant** (§5) — if this bothers the user later, a live check can be added as a follow-up story once a Jira REST client dependency is already justified by SH-58's own procedure body (which will need one anyway to actually create tickets).
- Token rotation procedure (`ALTER SECRET ... SET SECRET_STRING = ...`) — not built here; `CREATE SECRET IF NOT EXISTS` intentionally makes rotation a deliberate, separate action, not automatic.
- Jira custom fields (`RUL Hours`, `Requested By`) and priority-field mapping on `SUP` — SH-58's job (Module 9 §1), not provisioned here.
- Teardown: per Module 9 §3 and Module 10 §7, the Secret/Network Rule/EAI are **not** dropped by `09_teardown.sql` (external credential, matching the existing stance on the Jira project itself) — no change needed to the teardown script for this story.

---

## 9. File checklist

**New**:
- `scripts/01b_setup_jira.sql` (§3)

**Modified**:
- `manage.py` — new `setup_jira`/`run_setup_jira` command (§4), docstring note that it's a standalone, not-part-of-`up` stage.
- `scripts/README.md` — new table row + run-order note (§6).

**Reads only, no changes**: `scripts/01_setup.sql` (this story does not touch it — confirms §2's file-location decision didn't require any edit to the existing setup script).

---

## 10. Live-verified results (`Reviewer-agent`, 2026-09-27)

Clean PASS — implementation matches this doc exactly, no code deviations found. All 7 invariants (§7) held:

1. Token value never appears in a committed file, in `manage.py`'s printed/logged output, or in `01b_setup_jira.sql`'s literal text — only the `$jira_api_token` session-variable reference appears in the SQL file; `run_setup_jira()` binds the token via `cur.execute("SET jira_api_token = %s", (token,))`, never string-substituted.
2. `CREATE SECRET IF NOT EXISTS` confirmed (not `OR REPLACE`) — re-running the script against an already-provisioned environment does not require/clobber the existing secret; `NETWORK RULE`/`EXTERNAL ACCESS INTEGRATION` correctly remain `CREATE OR REPLACE`.
3. `NETWORK RULE` `VALUE_LIST` is exactly `('chirajpepz.atlassian.net:443')` — matches §2's confirmed hostname, not a placeholder (LLD Module 9 §3's own DDL sketch still shows `<jira-instance>.atlassian.net` as a placeholder — see the footnote added to `docs/04-9-LLD.md` §3 for this reconciliation).
4. No Jira project-creation call added anywhere — `SUP` reuse is documented-only, per §5, not live-checked.
5. No `SP_CREATE_JIRA_TICKET` procedure/field-mapping/duplicate-check logic added — provisioning-only scope respected.
6. `01b_setup_jira.sql` is a genuinely separate file from `01_setup.sql`.
7. `manage.py setup-jira` confirmed standalone — not called anywhere inside `run_up()`/`run_down()`.

**Files touched** (matches §9's checklist exactly, no additions or omissions):
- `scripts/01b_setup_jira.sql` — new, as specified in §3.
- `manage.py` — `run_setup_jira()` + `setup-jira` typer command added (§4), not folded into `run_up()`/`run_down()`.
- `scripts/README.md` — new `01b` row + run-order note (§6); one cosmetic nit (the `01b` row's position, initially appended after the table rather than immediately following row `01`) was found and fixed directly in the main session during review, not by `Developer-agent` — the row now sits in numeric order immediately after `01`, matching the file's existing convention.

**Key decisions confirmed as-built**: SM project (A5) reuses the existing `SUP` project on `chirajpepz.atlassian.net` — no new Jira project created or live-checked (§2, §5); the API token is passed as a runtime-bound SQL session variable (`SET jira_api_token = %s`) and never committed or string-substituted into SQL text (§4); `manage.py setup-jira` is a standalone command, deliberately not folded into `up` (§4's rationale — `up`/`down` must remain runnable without a human present to supply a token).
