-- ============================================================================
-- 09_teardown.sql — Full environment teardown (P3, deliberately last)
-- Traces to: FR-OPS-05, LLD Module 10 §7, docs/05-Epics.md EPIC-TEARDOWN §11
-- Jira: SH-68 (S-TEARDOWN-1), SH-61 (S-TEARDOWN-2)
-- Status: Partially built (database/role/warehouse only, matches what's
-- actually been created through SH-11) — extended as later stories add
-- agents/Streamlit.
--
-- Built so far: database (cascades schemas/tables/stage), role, warehouse --
-- the only objects any story has created up to SH-11 (S-DATA-2). Agent(s) /
-- Streamlit are NOT YET BUILT (EPIC-SKELETON SS4.5/4.6) -- their DROP
-- statements get added here once those stories exist, not before.
--
-- NOT dropped: the Jira Service Management project (A5) and its Secret /
-- External Access Integration — external system + credential, manual
-- cleanup, per FR-OPS-05.
--
-- USE ROLE ACCOUNTADMIN is required first: the connection's default role is
-- snowcomotive_role itself (the OAuth user's account-level default role),
-- and Snowflake refuses "DROP ROLE <current primary role>" -- discovered
-- 2026-08-30 running a real teardown, not a hypothetical.
-- ============================================================================

USE ROLE ACCOUNTADMIN;
DROP DATABASE IF EXISTS snowcomotive;
DROP ROLE IF EXISTS snowcomotive_role;
DROP WAREHOUSE IF EXISTS snowcomotive_wh;
