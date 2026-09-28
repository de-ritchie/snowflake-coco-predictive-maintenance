# Design: SH-58 — Simulated ticket store (S-JIRA-2, pivoted from real Jira REST)

Status: **Design frozen** (brainstorm confirmed by user, 2026-09-27) → `Developer-agent` build → **`Reviewer-agent` clean PASS (2026-09-27), all 9 checks held, including independent live re-verification of the dedupe logic** (see §12 for results). `Documenter-agent` reconciliation complete.
Epic: EPIC-JIRA | Story: SH-58 (S-JIRA-2: "SP_CREATE_JIRA_TICKET procedure")
Traces to: [docs/04-7-LLD.md](../04-7-LLD.md) §3 (tool spec/contract), [docs/04-9-LLD.md](../04-9-LLD.md) §1/§2 (field mapping, duplicate-check logic), [docs/designs/SH-53-jira-sm-provision-auth.md](SH-53-jira-sm-provision-auth.md) (superseded auth path, see §8)

**Pivot reason**: Live testing (2026-09-27, three independent trials — `snowcomotive_role`, `ACCOUNTADMIN` in the existing DB, `ACCOUNTADMIN` in a brand-new DB) conclusively proved `CREATE EXTERNAL ACCESS INTEGRATION` is blocked account-wide on this Trial-edition account (`<account-identifier>`), regardless of role/ownership/database — no stored procedure can make any outbound network call. **Decision: use a native Snowflake table instead of a real Jira REST call.** Postgres/EAI workarounds and account upgrade are explicitly out of scope — not revisited.

---

## 1. Scope

Implement the simulated ticket store as **four separate stored procedures**, each a distinct CRUD capability, backed by one native table:

1. `SP_CREATE_JIRA_TICKET` — create-or-dedupe (this is the one Module 7 §3's `create_jira_ticket`/`request_jira_ticket` agent tools call)
2. `SP_GET_JIRA_TICKET` — read a ticket by `ticket_id`
3. `SP_UPDATE_JIRA_TICKET_STATUS` — close/reopen a ticket
4. `SP_DELETE_JIRA_TICKET` — remove a ticket row

**Why four objects, not one multi-purpose procedure**: Cortex Agent tools are wired per-procedure (Module 7 already does this — `explain_prediction` vs. ticketing are separate tool/procedure pairs, and Plant Manager's agent config omits ticketing entirely). Splitting CRUD into separate procedures means enabling/disabling a capability for a given persona/agent is just including or omitting that procedure's tool in the agent's `tools` list — no conditional logic inside one giant procedure, no risk of over-granting a capability to a persona that shouldn't have it (e.g. Plant Manager should never get `SP_DELETE_JIRA_TICKET`, ever).

**Not in scope**: wiring these procedures into any agent's `tool_spec`/`tool_resources` (that's SH-56, a separate future story — Module 7 §3's YAML still stands as the contract for `SP_CREATE_JIRA_TICKET` specifically; `SP_GET/UPDATE/DELETE_JIRA_TICKET` aren't in Module 7 at all yet and may never need agent-tool wiring depending on what SH-56 decides).

---

## 2. Real-world decisions made during brainstorm

| Question | Decision |
|---|---|
| Real Jira REST call vs. native table? | **Native table** — `CREATE EXTERNAL ACCESS INTEGRATION` is permanently blocked on this account tier (confirmed 3x live, not transient). Closed discussion, not revisited. |
| Table placement | `snowcomotive.raw` — matching SH-53's Secret/Network Rule placement (environment/ops-scoped object, not a `cons`-layer consumption table), even though the auth objects it replaces are no longer needed for live Jira. |
| Procedures — one multi-purpose or split by capability? | **Split**: `SP_CREATE_JIRA_TICKET`, `SP_GET_JIRA_TICKET`, `SP_UPDATE_JIRA_TICKET_STATUS`, `SP_DELETE_JIRA_TICKET` — see §1's rationale. Procedures stay in `snowcomotive.cons` (unchanged from Module 7's existing identifier `SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET`) even though the backing table moved to `raw` — procedures are the consumption-facing/tool-callable surface; the table is the ops-scoped backing store, same split as e.g. `raw` landing objects vs. `cons` semantic-view-facing tables elsewhere in this project. |
| Naming — keep "JIRA" in the name? | **Keep JIRA in all four procedure names** — intentional placeholder framing for a future real-Jira swap, consistent across the whole CRUD set (not just create). |
| `ticket_id` scheme | **Sequence-based**, format `SIM-<n>` via a `SEQUENCE` object — reads like a ticket key in demo output, unambiguously not a real Jira key (no project-key prefix collision risk). |
| Ticket closing in scope? | **Yes** — `SP_UPDATE_JIRA_TICKET_STATUS` ships now so the `RECENTLY_CLOSED` duplicate-check path is actually exercisable in demo/testing, not just a theoretical column. |
| Ticket deletion in scope? | **Yes** — `SP_DELETE_JIRA_TICKET` ships now, for cleanup/testing convenience. Not wired to any agent tool by default (see §1). |
| `ticket_url` framing | **Omit/null** — no real external ticket exists, so nothing is returned that could be mistaken for a real link. `SP_CREATE_JIRA_TICKET`'s/`SP_GET_JIRA_TICKET`'s response always sets `ticket_url = NULL`. This is the contract SH-56 (future agent-tool wiring) must respect — agent instructions must not fabricate or imply a clickable URL. |
| Duplicate-check window | **Keep `RECENT_CLOSED_WINDOW_HOURS = 24`** — same MVP default as Module 9 §2's original live-JQL design, just re-implemented as a local table predicate instead of a JQL query. |
| SH-53's disposition | PR #24 **already merged** by the user (outside this design session's original plan). Not a problem: the Secret/Network Rule creation is harmless and idempotent; the `CREATE EXTERNAL ACCESS INTEGRATION` statement is expected to have failed (or been skipped) on this account tier, meaning that one object likely never got created — nothing in SH-58's new native-table design depends on it. Treated as a documented amendment, not reopened (§8). |
| Uncommitted dotenv changes (`manage.py` `load_dotenv()`, `pyproject.toml`/`uv.lock` `python-dotenv`) | Carry over onto the new `feature/SH-8-58-simulated-ticket-store` branch and commit there — generically useful, unrelated to the EAI blocker, PR #24 is already merged so they missed that window. **Action item for whoever creates the SH-58 branch** (`Jira-Triage-agent`, not this design session): `git stash` on `feature/SH-8-53-jira-sm-provision-auth` before branching, then `git stash pop` on the new branch. |

---

## 3. Table — `snowcomotive.raw.jira_ticket`

```sql
CREATE SEQUENCE IF NOT EXISTS snowcomotive.raw.jira_ticket_id_seq START = 1 INCREMENT = 1;

CREATE TABLE IF NOT EXISTS snowcomotive.raw.jira_ticket (
  ticket_id               STRING        NOT NULL,   -- 'SIM-' || jira_ticket_id_seq.NEXTVAL, set at insert time
  equipment_id             STRING        NOT NULL,
  predicted_rul_hours      FLOAT,
  priority_score           FLOAT,
  root_cause_summary       STRING,
  requested_by_persona     STRING,
  status                   STRING        NOT NULL DEFAULT 'OPEN',   -- 'OPEN' | 'CLOSED'
  created_ts               TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
  closed_ts                TIMESTAMP_NTZ,
  PRIMARY KEY (ticket_id)
);
```

`CREATE TABLE IF NOT EXISTS` / `CREATE SEQUENCE IF NOT EXISTS` — same idempotency stance as SH-53's `CREATE SECRET IF NOT EXISTS` (re-running setup must not clobber existing ticket history).

---

## 4. Procedure contracts

All four in `snowcomotive.cons`, Python stored procedures (no external access integration, no secrets — pure Snowflake DML against §3's table).

### 4.1 `SP_CREATE_JIRA_TICKET(equipment_id, predicted_rul_hours, priority_score, root_cause_summary, requested_by_persona)`

Same dedupe contract as Module 9 §2, re-implemented as a local query instead of live JQL:

```sql
-- Step 1: dup check
SELECT ticket_id, status, closed_ts FROM snowcomotive.raw.jira_ticket
WHERE equipment_id = :equipment_id
  AND (status = 'OPEN' OR closed_ts >= DATEADD('hour', -24, CURRENT_TIMESTAMP()))
ORDER BY created_ts DESC
LIMIT 1;
```

- Match found, `status = 'OPEN'` → return `{"status": "ALREADY_OPEN", "ticket_id": <existing>, "ticket_url": null}` — no insert.
- Match found, `status = 'CLOSED'` (within the 24h window) → return `{"status": "RECENTLY_CLOSED", "ticket_id": <existing>, "ticket_url": null}` — no insert.
- No match → insert new row with `ticket_id = 'SIM-' || snowcomotive.raw.jira_ticket_id_seq.NEXTVAL`, `status = 'OPEN'` → return `{"status": "CREATED", "ticket_id": <new>, "ticket_url": null}`.

`RECENT_CLOSED_WINDOW_HOURS = 24` as a named constant in the procedure body (not hardcoded inline twice) — same tunability intent as Module 9 §2.

### 4.2 `SP_GET_JIRA_TICKET(ticket_id)`

Returns the full row (`equipment_id`, `predicted_rul_hours`, `priority_score`, `root_cause_summary`, `requested_by_persona`, `status`, `created_ts`, `closed_ts`) for a given `ticket_id`, or a not-found indicator if no row matches. `ticket_url` is not a stored column — if this procedure's response shape mirrors the create contract, it must also return `ticket_url: null`, not omit the key inconsistently.

### 4.3 `SP_UPDATE_JIRA_TICKET_STATUS(ticket_id, new_status)`

`new_status` ∈ `{'OPEN', 'CLOSED'}`. Sets `status = :new_status`; sets `closed_ts = CURRENT_TIMESTAMP()` when transitioning to `CLOSED`, sets `closed_ts = NULL` when transitioning back to `OPEN` (reopen). Returns the updated row or a not-found indicator.

### 4.4 `SP_DELETE_JIRA_TICKET(ticket_id)`

Deletes the row by `ticket_id`. Returns a simple success/not-found indicator. Not wired to any agent tool by default (§1) — cleanup/testing use only.

---

## 5. Reconciliation with Module 7 / Module 9 LLD

- **Module 7 §3's `tool_resources.create_jira_ticket.identifier`** (`SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET`) is unchanged — the procedure's schema-qualified name and location match exactly what Module 7 already specifies. Only its *internal behavior* changes (table read/write instead of live JQL/REST). No LLD edit needed for the identifier itself.
- **Module 7 §3's comment** — "returns... ticket_url" — still holds structurally (the key exists in the response), but its value is now always `null`. A future `Documenter-agent` pass reconciling Module 7 should add a footnote (same pattern as SH-53's Module 9 §3 footnote) noting `ticket_url` is always `null` under the simulated-store implementation, and that `SP_GET/UPDATE/DELETE_JIRA_TICKET` are new procedures not originally described in Module 7's tool-spec table (they may or may not get their own agent tool wiring in SH-56 — undecided, out of this story's scope).
- **Module 9 §1 field mapping / §2 duplicate-check / §3 auth mechanism** — §1's field mapping still applies (same five inputs → same concepts, just columns instead of Jira fields/custom fields). §2's duplicate-check logic is functionally preserved (§4.1 above is a direct SQL re-expression of the same JQL predicate). §3's entire auth-mechanism section (Secret/Network Rule/EAI) is now **moot for SH-58's own purposes** — it's not deleted from the LLD (SH-53's objects may still exist per §8), but SH-58 does not declare `EXTERNAL_ACCESS_INTEGRATIONS`/`SECRETS` on any of its four procedures. `Documenter-agent` should add a footnote to Module 9 §3 noting this, parallel to SH-53's existing footnote.

---

## 6. Invariants for Reviewer-agent

1. None of the four procedures declare `EXTERNAL_ACCESS_INTEGRATIONS` or `SECRETS` — no outbound network call anywhere in this story's code (the whole point of the pivot).
2. `SP_CREATE_JIRA_TICKET`'s response `ticket_url` is always `null` — never a fabricated or real-looking URL string, in every returned status (`CREATED`, `ALREADY_OPEN`, `RECENTLY_CLOSED`).
3. Duplicate-check predicate matches §4.1 exactly: an `OPEN` ticket on the same `equipment_id` always short-circuits to `ALREADY_OPEN`; a `CLOSED` ticket on the same `equipment_id` within 24h of `closed_ts` short-circuits to `RECENTLY_CLOSED`; anything older or no match at all creates a new row and returns `CREATED`.
4. `ticket_id` values follow the `SIM-<n>` sequence format — never a bare integer, UUID, or anything resembling a real Jira key format (e.g. `SUP-123`).
5. `jira_ticket` table and `jira_ticket_id_seq` sequence live in `snowcomotive.raw`; all four procedures live in `snowcomotive.cons` — schema split matches §2's decision, not collapsed into one schema.
6. `CREATE TABLE IF NOT EXISTS` / `CREATE SEQUENCE IF NOT EXISTS` — re-running setup must not truncate or reset existing ticket history/sequence position.
7. `SP_DELETE_JIRA_TICKET` and `SP_UPDATE_JIRA_TICKET_STATUS` exist and are independently callable (not folded into `SP_CREATE_JIRA_TICKET`), per §1's split rationale.
8. No agent/tool_spec YAML changes are made in this story (Module 7's `tools`/`tool_resources` blocks are untouched) — that wiring is explicitly SH-56's job, not SH-58's.

---

## 7. `scripts/README.md` update

Whichever numbered setup script this story's `CREATE TABLE`/`CREATE SEQUENCE`/`CREATE PROCEDURE` statements land in (deferred to `Developer-agent` — check `scripts/README.md`'s existing run-order table for the right stage, likely alongside where other `cons`-layer procedure objects are created, e.g. near `07_post_setup.sql` or a dedicated ticket-store script) gets a row/note documenting these new objects, following the same convention as SH-53's `01b` row.

---

## 8. SH-53 amendment (separate from SH-58's own scope)

**This is an amendment to SH-53's disposition, not new SH-58 scope** — kept distinct per this design's instructions.

- PR #24 (SH-53) is **merged**. Its Secret (`snowcomotive.raw.jira_api_token`) and Network Rule (`snowcomotive.raw.jira_network_rule`) provisioning succeeded and is harmless/idempotent regardless of whether they're ever used again.
- The `CREATE EXTERNAL ACCESS INTEGRATION jira_access_integration` statement is now known to be **permanently blocked** on this Trial-edition account, not transiently — confirmed by this session's 3 independent live trials (main session context, not re-verified in this design conversation).
- **Decision**: no further script changes to `01b_setup_jira.sql` are made as part of SH-58. The EAI statement is left as-is (documented-expected-to-fail, or already skipped/failed silently at merge time — actual outcome not re-verified in this design session). SH-53 stays merged/provisioned as a historical artifact — useful again only if the account is ever upgraded off Trial edition. Nothing in EPIC-JIRA's remaining scope (SH-58, and whatever SH-56 turns out to need) depends on the Secret/Network Rule/EAI any longer.
- **Open item, not resolved here**: whether to add a short note to `docs/designs/SH-53-jira-sm-provision-auth.md` itself (e.g. a "§11 — Superseded" section) recording that SH-58 pivoted away from consuming this provisioning. Left for `Documenter-agent` or a follow-up edit, since this design doc is SH-58's, not SH-53's.

---

## 9. Handover: branch + uncommitted dotenv changes

**Not this design session's job to execute** (`Design-agent` never touches git) — flagged here so `Jira-Triage-agent` has the instruction in one place:

1. Check `git branch -a` for `feature/SH-8-58-simulated-ticket-store`; create it off `main` if absent.
2. Before switching off `feature/SH-8-53-jira-sm-provision-auth`, stash or otherwise preserve its uncommitted changes: `manage.py` (`load_dotenv()` call) and `pyproject.toml`/`uv.lock` (`python-dotenv` dependency).
3. Per §2's decision, carry those changes onto the new `feature/SH-8-58-simulated-ticket-store` branch and commit them there (generically useful `.env` support, unrelated to SH-58's own ticket-store scope, but PR #24 already merged so they missed that window).

---

## 10. File checklist (for `Developer-agent`)

**New** (exact filenames/locations deferred to `Developer-agent` per §7):
- A setup-script addition (new or existing numbered script) creating `snowcomotive.raw.jira_ticket_id_seq`, `snowcomotive.raw.jira_ticket`, and the four `snowcomotive.cons.SP_*_JIRA_TICKET` procedures (§3, §4).
- `scripts/README.md` row/note (§7).

**Modified**:
- `manage.py`/`pyproject.toml`/`uv.lock` — dotenv changes carried over from SH-53's branch (§9), unrelated to this story's own diff but landing on the same branch.

**Reads only, no changes**: `scripts/01b_setup_jira.sql`, `docs/04-9-LLD.md` §3, `docs/designs/SH-53-jira-sm-provision-auth.md` — SH-53's own artifacts are not edited by SH-58 (§8's amendment is documentation-only, recorded here, not as a code change to those files).

---

## 11. Explicitly deferred / open items

- Agent tool wiring for any of the four procedures (SH-56) — not this story.
- Whether `SP_GET/UPDATE/DELETE_JIRA_TICKET` ever get their own `tool_spec` entries, or stay internal/ops-only (called via Snowsight/manual SQL, not by an agent) — undecided, SH-56's call.
- A `Documenter-agent` footnote reconciling Module 7 §3 and Module 9 §3 with this pivot (§5) — not done in this design doc itself, left for the reconciliation pass after build.
- Whether to add a "Superseded" note to SH-53's own design doc (§8) — left open, not resolved here.
- Real Jira REST re-integration, if the Snowflake account is ever upgraded off Trial edition — explicitly out of scope, closed discussion per the pivot rationale.

---

## 12. Live-verified results (`Reviewer-agent`, 2026-09-27)

Clean PASS on all 9 checks — idempotency (`CREATE TABLE`/`CREATE SEQUENCE IF NOT EXISTS`, procedures `CREATE OR REPLACE`), `ticket_url` always `null` in every response of every procedure, dedupe predicate matches §4.1 exactly, no `EXTERNAL_ACCESS_INTEGRATIONS`/`SECRETS` declared anywhere (no outbound network call), all four procedures shipped as separate objects, `ticket_id` always `SIM-<n>` format, `found` used consistently as the not-found indicator across `SP_GET_JIRA_TICKET`/`SP_UPDATE_JIRA_TICKET_STATUS`/`SP_DELETE_JIRA_TICKET`, `manage.py` correctly left untouched by this story (agent-tool wiring is SH-56's job), and `scripts/README.md` accurately describes what was built. Verified both statically (reading the shipped SQL) and via independent live re-verification against `snow-co-cat-alyst-snowcomotive`.

**Files touched** (matches §10's file checklist exactly, no drift):
- New: `scripts/07b_setup_jira_ticket_store.sql` — `snowcomotive.raw.jira_ticket_id_seq` + `snowcomotive.raw.jira_ticket` (§3), all four `snowcomotive.cons.SP_*_JIRA_TICKET` procedures (§4). Run standalone, not folded into `manage.py up`.
- Modified: `scripts/README.md` — new row (08b) documenting the script, its objects, and its Jira reference (SH-58/S-JIRA-2).

**Live-verified dedupe scenario**: Reviewer-agent independently created a ticket for a test `equipment_id` against the live table (`CREATED`, new `SIM-<n>` row inserted with `status = 'OPEN'`), then re-invoked `SP_CREATE_JIRA_TICKET` for the same `equipment_id` and confirmed the dedupe predicate correctly short-circuited to `ALREADY_OPEN` with the same `ticket_id`, no second row inserted, `ticket_url: null` in both responses — matching §4.1/invariant 3 exactly.

**Flagged for SH-56 (not a spec violation, no code change made)**: `SP_UPDATE_JIRA_TICKET_STATUS`'s bad-`new_status` validation-error path (`new_status not in ("OPEN", "CLOSED")`) also returns `{"found": False, "error": ...}` — the same `found: False` shape used when the `ticket_id` row genuinely doesn't exist. A caller can't currently distinguish "invalid input" from "row doesn't exist" without separately checking for the presence of an `error` key. This design doc never specified invalid-input handling, so it isn't a deviation from anything frozen here — but whoever wires this procedure into an agent tool (SH-56) should be aware `found: False` is overloaded across two distinct failure modes, and should either check for `error` explicitly or ask for a distinct error path before assuming `found: False` always means "ticket not found."
