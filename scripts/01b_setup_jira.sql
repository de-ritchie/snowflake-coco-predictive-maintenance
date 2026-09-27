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
