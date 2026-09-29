-- ============================================================================
-- 02_setup_raw_ddl.sql — RAW table DDL
-- Traces to: FR-OPS-01, LLD Module 1 (RAW section), docs/05-Epics.md
-- EPIC-SKELETON §4.2, EPIC-FULLDATA
-- Jira: SH-11 (S-DATA-2), SH-34 (S-OPS-SETUP-2), SH-36 (S-DATA-8)
--
-- Creates the RAW tables. Columns/order match Module 1's RAW table spec
-- exactly — no extra audit columns.
--
-- RAW.EQUIPMENT/SENSOR_READING/CMMS_LOG: original thin-generator tables
-- (SH-13/SH-11).
--
-- RAW.SALES_ORDER/INVENTORY_FG_SNAPSHOT/SPARE_PART_SNAPSHOT/CALENDAR:
-- EPIC-FULLDATA additions (SH-34/SH-36), landing the full generator's
-- remaining bulk outputs.
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
  reading_value FLOAT,
  -- Real wall-clock load time. No column DEFAULT (Snowflake's ALTER TABLE
  -- ADD COLUMN doesn't support expression defaults like CURRENT_TIMESTAMP(),
  -- confirmed empirically, 2026-09-29 -- only CREATE TABLE column defs do,
  -- and this table must stay ALTERable for environments that already
  -- exist). Every loader (03_setup_raw_load.sql's bulk COPY INTO, and
  -- manage.py demo inject-tick/inject-batch's PUT+COPY INTO) explicitly
  -- runs an UPDATE ... SET insert_time = CURRENT_TIMESTAMP() WHERE
  -- insert_time IS NULL immediately after its COPY INTO instead.
  --
  -- Downstream dynamic tables' frozen region is keyed off THIS column, not
  -- reading_ts -- reading_ts is simulated event time that can be
  -- arbitrarily far in the past relative to real time (the live-tick
  -- generator replays historical dates), so a reading_ts-relative frozen
  -- cutoff permanently freezes out newly injected ticks once enough real
  -- time passes after `manage.py up`. insert_time tracks genuine load
  -- recency instead, so newly loaded rows are always active (non-frozen)
  -- immediately after loading, regardless of what date they simulate.
  -- Confirmed live, 2026-09-29.
  insert_time TIMESTAMP_NTZ
);

CREATE TABLE IF NOT EXISTS snowcomotive.raw.cmms_log (
  event_id STRING,
  equipment_id STRING,
  event_type STRING,
  event_start_ts TIMESTAMP_NTZ,
  event_end_ts TIMESTAMP_NTZ,
  technician_notes STRING
);

-- Key: (order_week, product_id, variant)
CREATE TABLE IF NOT EXISTS snowcomotive.raw.sales_order (
  order_week TIMESTAMP_NTZ,
  product_id STRING,
  variant STRING,
  order_units NUMBER(10,0)
);

-- Key: (snapshot_week, product_id, variant)
CREATE TABLE IF NOT EXISTS snowcomotive.raw.inventory_fg_snapshot (
  snapshot_week TIMESTAMP_NTZ,
  product_id STRING,
  variant STRING,
  fg_units_on_hand NUMBER(10,0)
);

-- Key: (snapshot_week, equipment_id, spare_part_name)
CREATE TABLE IF NOT EXISTS snowcomotive.raw.spare_part_snapshot (
  snapshot_week TIMESTAMP_NTZ,
  equipment_id STRING,
  spare_part_name STRING,
  units_on_hand NUMBER(10,0),
  lead_time_days NUMBER(10,0)
);

-- Key: calendar_date. Full-replace (static) -- see 03_setup_raw_load.sql's
-- idempotency note, this table is NOT append-only like the others.
CREATE TABLE IF NOT EXISTS snowcomotive.raw.calendar (
  calendar_date DATE,
  is_working_day BOOLEAN,
  is_holiday BOOLEAN
);
