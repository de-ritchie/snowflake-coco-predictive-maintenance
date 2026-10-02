# Changelog

## 2026-10-02 — SH-83: Remove hardcoded Snowflake account/username from profiles.yml and setup script

Parameterized `predictive_maintenance_dbt/profiles.yml`'s `account`/`user` via dbt's `env_var('SNOWFLAKE_DBT_ACCOUNT')`/`env_var('SNOWFLAKE_DBT_USER')` (no default — fails loud if unset) and replaced `scripts/01_setup.sql`'s hardcoded `GRANT ROLE snowcomotive_role TO USER CHIRAJ` with a `CURRENT_USER()`-driven grant, so any teammate with a different Snowflake account/user can run this repo without editing a committed file.
