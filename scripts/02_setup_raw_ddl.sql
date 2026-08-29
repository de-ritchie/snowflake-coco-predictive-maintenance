-- ============================================================================
-- 02_setup_raw_ddl.sql — RAW table DDL
-- Traces to: FR-OPS-01, LLD Module 1 (RAW section), docs/05-Epics.md
-- EPIC-SKELETON §4.2
-- Jira: SH-11 (S-DATA-2)
--
-- Creates the RAW tables needed to land SH-13's thin generator output:
-- RAW.EQUIPMENT, RAW.SENSOR_READING, RAW.CMMS_LOG. Columns/order match
-- Module 1's RAW table spec exactly — no extra audit columns.
--
-- RAW.CMMS_LOG is created here but not loaded by this story — SH-13's thin
-- generator produces no cmms_log.parquet (bearing-wear-only pass has nothing
-- to log). It stays empty until EPIC-FULLDATA's generator emits PM/breakdown
-- events.
--
-- Idempotent (FR-OPS-06): CREATE TABLE IF NOT EXISTS — safe to re-run.
--
-- Usage: snow sql -f scripts/02_setup_raw_ddl.sql --connection <your-connection>
-- Run after 01_setup.sql (requires snowcomotive.raw schema to exist).
-- ============================================================================

CREATE TABLE IF NOT EXISTS snowcomotive.raw.equipment (
  equipment_id STRING,
  equipment_name STRING,
  line_name STRING,
  product_id STRING,
  variant STRING,
  is_sensor_enabled BOOLEAN,
  throughput_units_per_hour NUMBER(10,2),
  commissioned_ts TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS snowcomotive.raw.sensor_reading (
  reading_id STRING,
  equipment_id STRING,
  reading_ts TIMESTAMP_NTZ,
  sensor_type STRING,
  reading_value FLOAT
);

CREATE TABLE IF NOT EXISTS snowcomotive.raw.cmms_log (
  event_id STRING,
  equipment_id STRING,
  event_type STRING,
  event_start_ts TIMESTAMP_NTZ,
  event_end_ts TIMESTAMP_NTZ,
  technician_notes STRING
);
