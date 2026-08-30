"""Environment lifecycle orchestrator (SH-2-11 follow-up) -- docker-compose-style
`up`/`down` wrapper around the numbered scripts/ SQL lifecycle files.

Uses the `snow-coco` OAuth connection (see AGENTS.md "Local dev environment") via
snowflake-connector-python directly -- no `snow` CLI dependency, so no repeated
MFA passcodes; the OAuth token is cached locally via `keyring`.

up:
  1. scripts/01_setup.sql        -- role, warehouse, database, schemas, stage
  2. generator/thin_sensor_generator.py -- produce ./output/*.parquet
  3. scripts/02_setup_raw_ddl.sql   -- RAW.EQUIPMENT / SENSOR_READING / CMMS_LOG DDL
  4. scripts/03_setup_raw_load.sql  -- PUT + COPY INTO the generated Parquet
  5. dbt seed, then dbt run --exclude tag:inference+ (predictive_maintenance_dbt/)
     -- Raw -> Standardized -> Consumption -> FEAST (SH-15/SH-20/SH-24/SH-26, SH-29).
     Seed must run before run now: the FEAST macro's baseline join references
     the seed via ref(), so a from-scratch env fails if run comes first.
  6. dbt run --select tag:inference+ (currently a no-op -- no model is tagged
     'inference' yet; wired now so S-MODEL-3 doesn't need this file re-edited)
  7. dbt test

down:
  1. scripts/09_teardown.sql -- drops database (cascades), role, warehouse

This only covers what's built so far (thin skeleton, EPIC-SKELETON P0) -- later
stories (models, semantic view, agents, Streamlit) extend `up`, not this
file's shape.
"""

import pathlib
import subprocess
import sys

import snowflake.connector
import typer

REPO_ROOT = pathlib.Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
GENERATOR = REPO_ROOT / "generator" / "thin_sensor_generator.py"
DBT_DIR = REPO_ROOT / "predictive_maintenance_dbt"
CONNECTION_NAME = "snow-coco"

# Default thin-skeleton demo window -- override via --start-date/--end-date.
DEFAULT_START_DATE = "2026-01-05"
DEFAULT_END_DATE = "2026-01-23"


def statements_from_sql_file(path: pathlib.Path) -> list[str]:
    """Strip full-line comments, then split the remaining SQL text on ';'."""
    lines = [
        line for line in path.read_text().splitlines() if not line.strip().startswith("--")
    ]
    raw = "\n".join(lines)
    return [s.strip() for s in raw.split(";") if s.strip()]


def run_sql_file(cur, path: pathlib.Path) -> None:
    print(f"--- Running {path.relative_to(REPO_ROOT)} ---")
    for stmt in statements_from_sql_file(path):
        print(f"  {stmt.splitlines()[0][:80]}...")
        cur.execute(stmt)
        for row in cur.fetchall():
            print(f"    {row}")


def generate_thin_data(start_date: str, end_date: str) -> None:
    print(f"--- Generating thin sensor data ({start_date} to {end_date}) ---")
    sys.path.insert(0, str(GENERATOR.parent))
    import thin_sensor_generator

    sys.argv = [
        "thin_sensor_generator.py",
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--output-dir",
        str(REPO_ROOT / "output"),
    ]
    thin_sensor_generator.main()


def run_dbt() -> None:
    # Shells out to the bare `dbt` binary -- only on PATH inside the uv venv,
    # so this script must be invoked as `uv run python manage.py up`, not a
    # bare `python manage.py up`.
    print("--- Running dbt seed ---")
    subprocess.run(["dbt", "seed"], cwd=DBT_DIR, check=True)
    # Phase 1: everything except model-inference tables (FR-OPS-02). Seed
    # must come first -- FEAST's baseline join ref()'s the seed (SH-29).
    print("--- Running dbt run --exclude tag:inference+ (phase 1: features) ---")
    subprocess.run(["dbt", "run", "--exclude", "tag:inference+"], cwd=DBT_DIR, check=True)
    # Phase 2 (FR-OPS-02b): model-inference tables. A no-op today -- no model
    # is tagged 'inference' yet (S-MODEL-3 not built) -- confirmed dbt exits
    # 0 on an empty selection, just warns. Model training (FR-OPS-02a,
    # S-MODEL-2) belongs between phase 1 and phase 2; not yet automated here.
    print("--- Running dbt run --select tag:inference+ (phase 2: inference) ---")
    subprocess.run(["dbt", "run", "--select", "tag:inference+"], cwd=DBT_DIR, check=True)
    print("--- Running dbt test ---")
    subprocess.run(["dbt", "test"], cwd=DBT_DIR, check=True)


def run_up(start_date: str, end_date: str) -> None:
    # role='ACCOUNTADMIN' overrides the snow-coco connection's default login
    # role (SNOWCOMOTIVE_ROLE itself) -- required because 01_setup.sql creates
    # that role; logging in as a role that doesn't exist yet is a deadlock,
    # hit for real after a full manage.py down (2026-08-30).
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="ACCOUNTADMIN")
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "01_setup.sql")
        generate_thin_data(start_date, end_date)
        run_sql_file(cur, SCRIPTS_DIR / "02_setup_raw_ddl.sql")
        run_sql_file(cur, SCRIPTS_DIR / "03_setup_raw_load.sql")
    finally:
        conn.close()
    run_dbt()
    print("--- up complete ---")


def run_down() -> None:
    # role='ACCOUNTADMIN' -- same deadlock avoidance as run_up(): 09_teardown.sql
    # drops snowcomotive_role, so the connection must not be logged in as it.
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="ACCOUNTADMIN")
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "09_teardown.sql")
    finally:
        conn.close()
    print("--- down complete ---")


app = typer.Typer(help=__doc__, no_args_is_help=True)


@app.command("up")
def up(
    start_date: str = typer.Option(DEFAULT_START_DATE, "--start-date", help="YYYY-MM-DD"),
    end_date: str = typer.Option(DEFAULT_END_DATE, "--end-date", help="YYYY-MM-DD"),
) -> None:
    """Spin up env + generate/load thin data."""
    run_up(start_date, end_date)


@app.command("down")
def down() -> None:
    """Tear down env (database/role/warehouse)."""
    run_down()


if __name__ == "__main__":
    app()
