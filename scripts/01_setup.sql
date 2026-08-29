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

-- Table-level grants (not covered by the schema-level grants above) — needed
-- because dbt's dynamic tables run as their owner role (snowcomotive_role)
-- and must be able to read every RAW table they reference, regardless of
-- which role originally created that table (see
-- docs/designs/2 - SH-2-15-20-24-26-dbt-scaffold-consumption.md, blocker log).
GRANT ALL ON ALL TABLES IN DATABASE snowcomotive TO ROLE snowcomotive_role;
GRANT ALL ON FUTURE TABLES IN DATABASE snowcomotive TO ROLE snowcomotive_role;

-- --- Stage for the data generator's Parquet landing zone (S-ENV-2) ---

CREATE STAGE IF NOT EXISTS snowcomotive.raw.landing_stage
  FILE_FORMAT = (TYPE = PARQUET);
GRANT READ, WRITE ON STAGE snowcomotive.raw.landing_stage TO ROLE snowcomotive_role;

-- ============================================================================
-- NEXT: RAW table DDL + thin data-gen upload/COPY INTO (SH-11 S-DATA-2,
-- SH-13 S-DATA-1) — implemented as two separate scripts rather than
-- appended here: scripts/02_setup_raw_ddl.sql (RAW.EQUIPMENT /
-- RAW.SENSOR_READING / RAW.CMMS_LOG DDL) and scripts/03_setup_raw_load.sql
-- (PUT + COPY INTO for the generator's Parquet output).
-- ============================================================================
