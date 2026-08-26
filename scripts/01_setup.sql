-- ============================================================================
-- 01_setup.sql — Environment setup script
-- Traces to: FR-OPS-01, LLD Module 10 §1, docs/05-Epics.md EPIC-SKELETON §4.1
-- Jira: SH-10 (S-ENV-1), SH-14 (S-ENV-2) — DONE
--
-- This is a VERSIONED, GROWING script (per docs/05-Epics.md §2's "scripts as
-- living artifacts" philosophy) — it does NOT get replaced, it gets EXTENDED
-- in place at each tier boundary:
--   v0 (current): role, warehouse, database, schemas, stage (this file's scope)
--   v1 (EPIC-SKELETON §4.2, SH-11/SH-13): + thin data-gen invocation / COPY INTO
--   v2 (EPIC-FULLDATA, S-OPS-SETUP-2): + full FR-DG-12 generator call,
--       --reuse-dataset-path/--seed flags (NFR-05)
--   v2 (EPIC-JIRA, S-JIRA-1): + Jira SM project provisioning check
--
-- Idempotent (FR-OPS-06): every statement uses IF NOT EXISTS — safe to
-- re-run against an existing environment without failing or duplicating
-- objects.
--
-- Usage: snow sql -f scripts/01_setup.sql --connection <your-connection>
-- (or run directly via the Snowflake CLI / Snowsight worksheet — no
-- variables to substitute at this stage)
-- ============================================================================

-- --- Role, warehouse, database, schemas (S-ENV-1) ---

CREATE ROLE IF NOT EXISTS snowcomotive_role;
GRANT ROLE snowcomotive_role TO USER CHIRAJ;

CREATE WAREHOUSE IF NOT EXISTS snowcomotive_wh
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
GRANT USAGE, OPERATE ON WAREHOUSE snowcomotive_wh TO ROLE snowcomotive_role;

CREATE DATABASE IF NOT EXISTS snowcomotive;
GRANT ALL ON DATABASE snowcomotive TO ROLE snowcomotive_role;

CREATE SCHEMA IF NOT EXISTS snowcomotive.raw;
CREATE SCHEMA IF NOT EXISTS snowcomotive.std;
CREATE SCHEMA IF NOT EXISTS snowcomotive.cons;
CREATE SCHEMA IF NOT EXISTS snowcomotive.feast;

GRANT ALL ON ALL SCHEMAS IN DATABASE snowcomotive TO ROLE snowcomotive_role;
GRANT ALL ON FUTURE SCHEMAS IN DATABASE snowcomotive TO ROLE snowcomotive_role;

-- --- Stage for the data generator's Parquet landing zone (S-ENV-2) ---

CREATE STAGE IF NOT EXISTS snowcomotive.raw.landing_stage
  FILE_FORMAT = (TYPE = PARQUET);
GRANT READ, WRITE ON STAGE snowcomotive.raw.landing_stage TO ROLE snowcomotive_role;

-- ============================================================================
-- NEXT (not yet in this file): thin data-gen invocation + COPY INTO Raw
-- tables (SH-11 S-DATA-2, SH-13 S-DATA-1) — the Snowpark generator script and
-- its Stage-upload step are a separate Python artifact (see LLD Module 2),
-- invoked from a wrapper that will be appended here once S-DATA-1/2 build it.
-- ============================================================================
