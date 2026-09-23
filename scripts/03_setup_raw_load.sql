-- ============================================================================
-- 03_setup_raw_load.sql — Upload + load full generator output into RAW
-- Traces to: FR-OPS-01, LLD Module 1 (RAW section), Module 10 §1/§8,
-- docs/05-Epics.md EPIC-SKELETON §4.2, EPIC-FULLDATA
-- Jira: SH-11 (S-DATA-2), SH-34 (S-OPS-SETUP-2), SH-36 (S-DATA-8)
--
-- Prerequisite (run by manage.py up, in-process): generator/full_data_generator.py
-- produces ./output/{equipment,sensor_reading,cmms_log,sales_order,
-- inventory_fg_snapshot,spare_part_snapshot,calendar}.parquet (paths relative
-- to repo root — run from repo root, matching the generator's default
-- --output-dir ./output).
--
-- Idempotency: relies on COPY INTO's native load-history dedup (Module 10
-- §8) — no FORCE=TRUE, no truncate-first logic. Re-running against
-- already-loaded files is a no-op. EXCEPTION: RAW.CALENDAR (see below).
--
-- ON_ERROR = 'ABORT_STATEMENT' chosen over CONTINUE/SKIP_FILE — this is a
-- thin, single-file, dev-time load; a malformed file should fail loudly.
--
-- Usage: snow sql -f scripts/03_setup_raw_load.sql --connection <your-connection>
-- Run from the repo root, after 01_setup.sql and 02_setup_raw_ddl.sql.
-- ============================================================================

PUT 'file://./output/equipment.parquet' @snowcomotive.raw.landing_stage/equipment/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/sensor_reading.parquet' @snowcomotive.raw.landing_stage/sensor_reading/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/cmms_log.parquet' @snowcomotive.raw.landing_stage/cmms_log/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/sales_order.parquet' @snowcomotive.raw.landing_stage/sales_order/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/inventory_fg_snapshot.parquet' @snowcomotive.raw.landing_stage/inventory_fg_snapshot/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/spare_part_snapshot.parquet' @snowcomotive.raw.landing_stage/spare_part_snapshot/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/calendar.parquet' @snowcomotive.raw.landing_stage/calendar/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

COPY INTO snowcomotive.raw.equipment
  FROM @snowcomotive.raw.landing_stage/equipment/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.sensor_reading
  FROM @snowcomotive.raw.landing_stage/sensor_reading/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.cmms_log
  FROM @snowcomotive.raw.landing_stage/cmms_log/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.sales_order
  FROM @snowcomotive.raw.landing_stage/sales_order/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.inventory_fg_snapshot
  FROM @snowcomotive.raw.landing_stage/inventory_fg_snapshot/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

COPY INTO snowcomotive.raw.spare_part_snapshot
  FROM @snowcomotive.raw.landing_stage/spare_part_snapshot/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT';

-- RAW.CALENDAR is full-replace (static), not append-only -- TRUNCATE + FORCE=TRUE
-- bypass COPY INTO's native load-history dedup, which would otherwise make a
-- second setup run on regenerated calendar data a silent no-op. This
-- exception applies ONLY to RAW.CALENDAR -- every other table above relies
-- on native dedup, no truncate.
TRUNCATE TABLE snowcomotive.raw.calendar;
COPY INTO snowcomotive.raw.calendar
  FROM @snowcomotive.raw.landing_stage/calendar/
  FILE_FORMAT = (TYPE = PARQUET)
  MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
  ON_ERROR = 'ABORT_STATEMENT'
  FORCE = TRUE;
