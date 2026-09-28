-- ============================================================================
-- 07c_setup_jira_mcp.sql — MCP Jira integration (S-STRETCH-2, SH-62)
-- Traces to: docs/designs/SH-62-mcp-swap-jira.md §3/§5 (original), §14.3
-- (amendment, 2026-09-28), docs/designs/SH-53-jira-sm-provision-auth.md
-- (superseded real-Jira-REST auth path this replaces the auth *mechanism*
-- for), docs/designs/SH-58-simulated-ticket-store.md (the simulated store
-- this originally coexisted with -- since retired, see below)
-- Jira: SH-62
-- Status: Built (v2 -- SH-62 amendment, 2026-09-28: the `real_ticket_key`
-- ALTER TABLE and SP_LINK_MCP_TICKET procedure were removed from this
-- script now that the simulated store they cross-referenced has been
-- retired entirely (scripts/07d_retire_simulated_ticket_store.sql, design
-- doc §14.3). This script now only creates the API integration/MCP server
-- pair -- v1's cross-reference plumbing is gone, not just unused.
--
-- Creates the real-Jira-backed ticketing path (API integration + MCP
-- server). It no longer touches the simulated store in any way -- that
-- store (07b_setup_jira_ticket_store.sql) has been retired entirely
-- (07d_retire_simulated_ticket_store.sql, design doc §14).
--
-- `jira_mcp_integration` / `snowcomotive.cons.jira_mcp_server` use
-- IF NOT EXISTS, not OR REPLACE -- once a human has completed the one-time
-- OAuth consent flow (manage.py authorize-jira-mcp) against a given
-- integration/server pair, re-running this script must not force them
-- through consent again (design doc §3/§10 invariant 5). This differs from
-- 07_post_setup.sql's CREATE OR REPLACE AGENT convention -- those objects
-- have no attached credential state to lose on replace; this one does.
--
-- Run standalone (not part of `manage.py up`, and not wired into
-- `manage.py post-setup` either) -- same "07/07b precedent" as
-- 07b_setup_jira_ticket_store.sql, which likewise has no dedicated
-- manage.py command. Run directly against the snow-co-cat-alyst connection.
--
-- DEVIATION FROM DESIGN DOC (confirmed live, this session): the design
-- doc's sketch assumed `USE ROLE snowcomotive_role` for the whole script,
-- matching 01b_setup_jira.sql's pattern. Live-tested against this account:
-- `CREATE API INTEGRATION` is an ACCOUNT-level object and snowcomotive_role
-- has no CREATE INTEGRATION grant on ACCOUNT (confirmed via
-- "Insufficient privileges... must have CREATE API INTEGRATION granted on
-- ACCOUNT" when attempted as snowcomotive_role) -- so that one statement
-- (plus its own GRANT USAGE) runs as ACCOUNTADMIN, then the script
-- switches back to snowcomotive_role for the schema-scoped MCP server
-- object, the table ALTER, and the procedure (all of which succeeded as
-- snowcomotive_role, matching every other object this project creates).
-- ============================================================================

USE ROLE ACCOUNTADMIN;

-- --- API Integration: authorizes Snowflake to reach mcp.atlassian.com ---
-- Account-level, unqualified -- same pattern as SH-53's jira_access_integration,
-- with `_mcp_` inserted to distinguish the two auth mechanisms unambiguously
-- in SHOW INTEGRATIONS output (design doc §2).
CREATE API INTEGRATION IF NOT EXISTS jira_mcp_integration
  API_PROVIDER = external_mcp
  API_ALLOWED_PREFIXES = ('https://mcp.atlassian.com')
  API_USER_AUTHENTICATION = (TYPE = OAUTH_DYNAMIC_CLIENT, OAUTH_RESOURCE_URL = 'https://mcp.atlassian.com/v1/mcp')
  ENABLED = TRUE;

GRANT USAGE ON INTEGRATION jira_mcp_integration TO ROLE snowcomotive_role;

USE ROLE snowcomotive_role;

-- --- External MCP Server: wraps the integration, points at the Atlassian
-- hosted MCP endpoint ---
-- Schema-qualified, lives in `snowcomotive.cons` -- agent-adjacent
-- infrastructure (design doc §2), not `raw` (unlike SH-53's now-superseded
-- Secret/Network Rule, which was environment-scoped auth living in `raw`).
CREATE EXTERNAL MCP SERVER IF NOT EXISTS snowcomotive.cons.jira_mcp_server
  WITH DISPLAY_NAME = 'Jira MCP (Atlassian hosted)'
  URL = 'https://mcp.atlassian.com/v1/mcp'
  API_INTEGRATION = jira_mcp_integration;
