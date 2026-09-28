-- ============================================================================
-- 07b_setup_jira_ticket_store.sql — Simulated Jira ticket store (S-JIRA-2, SH-58)
-- Traces to: FR-JR-01/03/04, docs/04-7-LLD.md §3, docs/04-9-LLD.md §1/§2,
-- docs/designs/SH-58-simulated-ticket-store.md
-- Jira: SH-58
-- Status: Built (v1)
--
-- RETIRED (SH-62 amendment, 2026-09-28) — see docs/designs/SH-62-mcp-swap-
-- jira.md §14 and scripts/07d_retire_simulated_ticket_store.sql. This file
-- is left in place as historical record of the original design and is
-- never run again; its objects no longer exist live once
-- 07d_retire_simulated_ticket_store.sql has been run.
--
-- Pivot from SH-53's real-Jira-REST design: CREATE EXTERNAL ACCESS INTEGRATION
-- is permanently blocked on this Trial-edition account (confirmed 3x live,
-- see design doc's pivot rationale), so this creates a native table instead
-- of calling out to Jira at all. None of the four procedures below declare
-- EXTERNAL_ACCESS_INTEGRATIONS or SECRETS -- no outbound network call
-- anywhere in this script.
--
-- Table/sequence live in `raw` (ops-scoped backing store, matching SH-53's
-- Secret/Network Rule placement); the four procedures stay in `cons`
-- (consumption-facing/tool-callable surface) -- SNOWCOMOTIVE.CONS.SP_CREATE_
-- JIRA_TICKET is the exact identifier Module 7 §3's `create_jira_ticket`/
-- `request_jira_ticket` agent tools already point at; only its internal
-- behavior changes here (table read/write instead of live JQL/REST).
--
-- `ticket_url` is always NULL in every response, every procedure -- no real
-- Jira ticket exists, and nothing returned here should be mistaken for a
-- real link (design doc §2, invariant #2).
--
-- `ticket_id` values are always `SIM-<n>` via jira_ticket_id_seq -- never a
-- bare integer/UUID/real-looking Jira key (design doc invariant #4).
--
-- Idempotent (FR-OPS-06): CREATE TABLE/SEQUENCE IF NOT EXISTS -- re-running
-- this script must not truncate ticket history or reset the sequence
-- position. Procedures use CREATE OR REPLACE (no state, safe to redefine).
--
-- Not wired into manage.py's `up` sequence or any agent tool_spec by this
-- story (design doc §10's file checklist does not include a manage.py
-- change for this script) -- run standalone for now; agent-tool wiring is
-- SH-56's job, not this story's.
--
-- Usage: run via manage.py's SQL-file runner, or directly against the
-- `snow-co-cat-alyst` connection, as `snowcomotive_role`.
-- ============================================================================

USE ROLE snowcomotive_role;

-- --- Sequence + table: snowcomotive.raw.jira_ticket ---

CREATE SEQUENCE IF NOT EXISTS snowcomotive.raw.jira_ticket_id_seq START = 1 INCREMENT = 1;

CREATE TABLE IF NOT EXISTS snowcomotive.raw.jira_ticket (
  ticket_id               STRING        NOT NULL,   -- 'SIM-' || jira_ticket_id_seq.NEXTVAL, set at insert time
  equipment_id            STRING        NOT NULL,
  predicted_rul_hours     FLOAT,
  priority_score          FLOAT,
  root_cause_summary      STRING,
  requested_by_persona    STRING,
  status                  STRING        NOT NULL DEFAULT 'OPEN',   -- 'OPEN' | 'CLOSED'
  created_ts              TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
  closed_ts               TIMESTAMP_NTZ,
  PRIMARY KEY (ticket_id)
);

-- --- Procedures: snowcomotive.cons.SP_*_JIRA_TICKET ---
-- Four separate objects (not one multi-purpose procedure) so a future
-- per-persona agent tool wiring (SH-56) can enable/disable each capability
-- independently -- e.g. Plant Manager must never get SP_DELETE_JIRA_TICKET.

-- SP_CREATE_JIRA_TICKET: create-or-dedupe. This is the procedure Module 7
-- §3's create_jira_ticket/request_jira_ticket agent tools call.
CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_create_jira_ticket(
  equipment_id STRING,
  predicted_rul_hours FLOAT,
  priority_score FLOAT,
  root_cause_summary STRING,
  requested_by_persona STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'create_ticket'
AS
$$
# RECENT_CLOSED_WINDOW_HOURS: tunable, same MVP default as Module 9 §2's
# original live-JQL design, just re-implemented as a local table predicate.
RECENT_CLOSED_WINDOW_HOURS = 24


def create_ticket(session, equipment_id, predicted_rul_hours, priority_score, root_cause_summary, requested_by_persona):
    # Step 1: dup check -- open ticket on this equipment_id always wins,
    # else a ticket closed within the recent window.
    existing = session.sql(
        f"""
        SELECT ticket_id, status
        FROM snowcomotive.raw.jira_ticket
        WHERE equipment_id = ?
          AND (status = 'OPEN' OR closed_ts >= DATEADD('hour', -{RECENT_CLOSED_WINDOW_HOURS}, CURRENT_TIMESTAMP()))
        ORDER BY created_ts DESC
        LIMIT 1
        """,
        params=[equipment_id],
    ).collect()

    if existing:
        row = existing[0]
        status = "ALREADY_OPEN" if row["STATUS"] == "OPEN" else "RECENTLY_CLOSED"
        return {"status": status, "ticket_id": row["TICKET_ID"], "ticket_url": None}

    # Step 2: no match -- create.
    new_id_row = session.sql(
        "SELECT 'SIM-' || snowcomotive.raw.jira_ticket_id_seq.NEXTVAL AS TICKET_ID"
    ).collect()
    ticket_id = new_id_row[0]["TICKET_ID"]

    session.sql(
        """
        INSERT INTO snowcomotive.raw.jira_ticket
          (ticket_id, equipment_id, predicted_rul_hours, priority_score, root_cause_summary, requested_by_persona, status)
        VALUES (?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        params=[ticket_id, equipment_id, predicted_rul_hours, priority_score, root_cause_summary, requested_by_persona],
    ).collect()

    return {"status": "CREATED", "ticket_id": ticket_id, "ticket_url": None}
$$;

-- SP_GET_JIRA_TICKET: read a ticket by ticket_id. Response shape mirrors
-- create's contract (ticket_url always null) but uses `found` (not `status`)
-- as the not-found indicator, since `status` here means the ticket's own
-- OPEN/CLOSED column value, not a create-style result code.
CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_get_jira_ticket(
  ticket_id STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'get_ticket'
AS
$$
def get_ticket(session, ticket_id):
    rows = session.sql(
        """
        SELECT ticket_id, equipment_id, predicted_rul_hours, priority_score,
               root_cause_summary, requested_by_persona, status, created_ts, closed_ts
        FROM snowcomotive.raw.jira_ticket
        WHERE ticket_id = ?
        """,
        params=[ticket_id],
    ).collect()

    if not rows:
        return {"found": False, "ticket_id": ticket_id, "ticket_url": None}

    row = rows[0]
    return {
        "found": True,
        "ticket_id": row["TICKET_ID"],
        "equipment_id": row["EQUIPMENT_ID"],
        "predicted_rul_hours": row["PREDICTED_RUL_HOURS"],
        "priority_score": row["PRIORITY_SCORE"],
        "root_cause_summary": row["ROOT_CAUSE_SUMMARY"],
        "requested_by_persona": row["REQUESTED_BY_PERSONA"],
        "status": row["STATUS"],
        "created_ts": str(row["CREATED_TS"]),
        "closed_ts": str(row["CLOSED_TS"]) if row["CLOSED_TS"] else None,
        "ticket_url": None,
    }
$$;

-- SP_UPDATE_JIRA_TICKET_STATUS: close/reopen a ticket. new_status must be
-- 'OPEN' or 'CLOSED'; closed_ts is set on close, cleared on reopen.
CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_update_jira_ticket_status(
  ticket_id STRING,
  new_status STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'update_status'
AS
$$
def update_status(session, ticket_id, new_status):
    if new_status not in ("OPEN", "CLOSED"):
        return {"found": False, "error": f"new_status must be 'OPEN' or 'CLOSED', got: {new_status}"}

    if new_status == "CLOSED":
        session.sql(
            "UPDATE snowcomotive.raw.jira_ticket SET status = 'CLOSED', closed_ts = CURRENT_TIMESTAMP() WHERE ticket_id = ?",
            params=[ticket_id],
        ).collect()
    else:
        session.sql(
            "UPDATE snowcomotive.raw.jira_ticket SET status = 'OPEN', closed_ts = NULL WHERE ticket_id = ?",
            params=[ticket_id],
        ).collect()

    rows = session.sql(
        "SELECT ticket_id, equipment_id, status, created_ts, closed_ts FROM snowcomotive.raw.jira_ticket WHERE ticket_id = ?",
        params=[ticket_id],
    ).collect()

    if not rows:
        return {"found": False, "ticket_id": ticket_id}

    row = rows[0]
    return {
        "found": True,
        "ticket_id": row["TICKET_ID"],
        "status": row["STATUS"],
        "closed_ts": str(row["CLOSED_TS"]) if row["CLOSED_TS"] else None,
    }
$$;

-- SP_DELETE_JIRA_TICKET: remove a ticket row. Cleanup/testing use only --
-- not wired to any agent tool by default (design doc §1).
CREATE OR REPLACE PROCEDURE snowcomotive.cons.sp_delete_jira_ticket(
  ticket_id STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'delete_ticket'
AS
$$
def delete_ticket(session, ticket_id):
    existing = session.sql(
        "SELECT ticket_id FROM snowcomotive.raw.jira_ticket WHERE ticket_id = ?",
        params=[ticket_id],
    ).collect()

    if not existing:
        return {"found": False, "ticket_id": ticket_id, "deleted": False}

    session.sql(
        "DELETE FROM snowcomotive.raw.jira_ticket WHERE ticket_id = ?",
        params=[ticket_id],
    ).collect()

    return {"found": True, "ticket_id": ticket_id, "deleted": True}
$$;
