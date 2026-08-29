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

down:
  1. scripts/09_teardown.sql -- drops database (cascades), role, warehouse

This only covers what's built so far (thin skeleton, EPIC-SKELETON P0) -- later
stories (dbt, models, semantic view, agents, Streamlit) extend `up`, not this
file's shape.
"""

import pathlib
import sys

import snowflake.connector
import typer

REPO_ROOT = pathlib.Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
GENERATOR = REPO_ROOT / "generator" / "thin_sensor_generator.py"
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


def run_up(start_date: str, end_date: str) -> None:
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "01_setup.sql")
        generate_thin_data(start_date, end_date)
        run_sql_file(cur, SCRIPTS_DIR / "02_setup_raw_ddl.sql")
        run_sql_file(cur, SCRIPTS_DIR / "03_setup_raw_load.sql")
    finally:
        conn.close()
    print("--- up complete ---")


def run_down() -> None:
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME)
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
