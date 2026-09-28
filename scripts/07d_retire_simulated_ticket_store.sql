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
