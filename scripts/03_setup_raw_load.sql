-- ============================================================================
-- 03_setup_raw_load.sql — Upload + load thin generator output into RAW
-- Traces to: FR-OPS-01, LLD Module 1 (RAW section), Module 10 §1/§8,
-- docs/05-Epics.md EPIC-SKELETON §4.2
-- Jira: SH-11 (S-DATA-2)
--
-- Prerequisite (manual, not automated by this script):
--   python generator/thin_sensor_generator.py --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
-- produces ./output/equipment.parquet and ./output/sensor_reading.parquet
-- (paths relative to repo root — run from repo root, matching the
-- generator's default --output-dir ./output).
--
-- No COPY INTO for RAW.CMMS_LOG — SH-13's thin generator produces no
-- cmms_log.parquet (bearing-wear-only pass has nothing to log). Table
-- exists (02_setup_raw_ddl.sql) but stays empty until EPIC-FULLDATA's
-- generator emits PM/breakdown events.
--
-- Idempotency: relies on COPY INTO's native load-history dedup (Module 10
-- §8) — no FORCE=TRUE, no truncate-first logic. Re-running against
-- already-loaded files is a no-op.
--
-- ON_ERROR = 'ABORT_STATEMENT' chosen over CONTINUE/SKIP_FILE — this is a
-- thin, single-file, dev-time load; a malformed file should fail loudly.
--
-- Usage: snow sql -f scripts/03_setup_raw_load.sql --connection <your-connection>
-- Run from the repo root, after 01_setup.sql and 02_setup_raw_ddl.sql.
-- ============================================================================

PUT 'file://./output/equipment.parquet' @snowcomotive.raw.landing_stage/equipment/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
PUT 'file://./output/sensor_reading.parquet' @snowcomotive.raw.landing_stage/sensor_reading/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE;

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

-- No COPY INTO for cmms_log — SH-13's thin generator produces no cmms_log.parquet
-- (bearing-wear-only pass has nothing to log). Table exists (02) but stays empty
-- until EPIC-FULLDATA's generator emits PM/breakdown events.
