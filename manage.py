"""Environment lifecycle orchestrator (SH-2-11 follow-up, SH-34/36/41 v2)
-- docker-compose-style `up`/`down` wrapper around the numbered scripts/ SQL
lifecycle files, plus a `demo` sub-command group for the live-tick drip-feed.

Uses the `snow-coco` OAuth connection (see AGENTS.md "Local dev environment") via
snowflake-connector-python directly -- no `snow` CLI dependency, so no repeated
MFA passcodes; the OAuth token is cached locally via `keyring`.

up:
  1. scripts/01_setup.sql        -- role, warehouse, database, schemas, stage
     (runs as ACCOUNTADMIN -- the role doesn't exist yet to log in as)
  2. USE ROLE snowcomotive_role -- everything below owns its objects as
     snowcomotive_role from here on, matching dbt's own connection; running
     as ACCOUNTADMIN throughout caused stored procedures it creates to lack
     SELECT on tables owned by snowcomotive_role (hit for real, SH-22).
  3. generator/full_data_generator.py's run_simulation() -- produce
     ./output/*.parquet (7 bulk tables incl. calendar) + ./output/live_ticks/
     (trailing-30-day per-tick files for `demo inject-tick`, untouched by up).
     Supersedes the thin generator (SH-34) -- there is no thin-data path left
     in `up`; generator/thin_sensor_generator.py itself is left untouched as
     a standalone reference script.
  4. scripts/02_setup_raw_ddl.sql   -- RAW table DDL (7 tables incl. the
     EPIC-FULLDATA additions: sales_order, inventory_fg_snapshot,
     spare_part_snapshot, calendar)
  5. scripts/03_setup_raw_load.sql  -- PUT + COPY INTO the generated Parquet
     (RAW.CALENDAR full-replace via TRUNCATE + FORCE=TRUE; every other table
     relies on COPY INTO's native load-history dedup)
  6. dbt seed, then dbt run --exclude tag:inference+ --exclude
     feast__training_dataset_rul (predictive_maintenance_dbt/) -- Raw ->
     Standardized -> Consumption -> FEAST (SH-15/SH-20/SH-24/SH-26, SH-29),
     minus feast__training_dataset_rul (SH-50 -- that model calls
     MODEL(isolation_forest_model) directly, so it must build after step 7,
     not here). Seed must run before run now: the FEAST macro's baseline join
     references the seed via ref(), so a from-scratch env fails if run comes
     first.
  7. scripts/05_train_models.sql -- CREATE OR REPLACE PROCEDURE + CALL
     sp_train_isolation_forest() (SH-22/S-MODEL-2), between the two dbt
     phases per FR-OPS-02a (inference tables reference the model by name).
  7a. dbt run --select feast__training_dataset_rul (SH-50) -- now safe to
      build; isolation_forest_model exists as of step 7.
  7b. scripts/06_train_rul_model.sql (SH-44/S-RUL-3) -- CREATE OR REPLACE
      PROCEDURE + CALL sp_train_rul_aft_model(), trains/promotes
      rul_aft_model on feast.training_dataset_rul (built as of step 7a).
      Must run strictly after 7a and strictly before step 8
      (docs/designs/SH-44-train-rul-aft-model.md §2).
  8. dbt run --select tag:inference+ (S-MODEL-3: cons__fct_anomaly_result,
     tags=['inference'] -- confirmed insertedRows:1/copiedRows:0 on a single
     new tick, true incremental refresh cascading through this layer too)
  9. dbt test
  10. scripts/07_post_setup.sql (SH-30/27/28/31: semantic view + agent +
      streamlit_stage), then PUT oee_command_center_app/*.py (recursively,
      preserving pages/ subpath) to @snowcomotive.cons.streamlit_stage, then
      CREATE OR REPLACE STREAMLIT snowcomotive.cons.oee_command_center --
      SH-33's deviation from `snow streamlit deploy` (rejected CLI, see
      AGENTS.md): native CREATE STREAMLIT DDL + connector-session PUT only,
      same pattern as `demo inject-tick`'s own PUT+COPY INTO.

down:
  1. scripts/09_teardown.sql -- drops database (cascades), role, warehouse
     (runs as ACCOUNTADMIN -- 09_teardown.sql drops snowcomotive_role itself)

demo (SH-41 / S-DATA-9): no CREATE TASK/EXECUTE TASK -- direct synchronous
PUT + COPY INTO for a single live_ticks/ file per invocation, deliberately
simpler than Module 10 §6's literal pseudocode (frozen design doc SH-34-36-41
§6):
  inject-tick   -- injects the next tick file after output/live_ticks/.cursor
                   into RAW.SENSOR_READING, then advances the cursor (only
                   after COPY INTO confirms success -- safe to re-run after a
                   partial failure).
  inject-batch  -- injects every tick file sharing the next not-yet-loaded
                   file's calendar date (one PUT + one COPY INTO for the
                   whole date-chunk), advancing the cursor to the chunk's
                   last file only after COPY INTO confirms LOADED for every
                   file (SH-42, T4). With LIVE_WINDOW_WORKING_DAYS=2
                   (simulate.py, T3) there are always exactly 2 such
                   date-chunks, so 2 calls drain the whole live window.
  reset-cursor  -- clears the cursor, restarting the drip-feed from the first
                   tick for a repeat demo run.

"""

import os
import pathlib
import subprocess
from datetime import date

import numpy as np
import snowflake.connector
import typer

REPO_ROOT = pathlib.Path(__file__).resolve().parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
OUTPUT_DIR = REPO_ROOT / "output"
LIVE_TICKS_DIR = OUTPUT_DIR / "live_ticks"
CURSOR_FILE = LIVE_TICKS_DIR / ".cursor"
DBT_DIR = REPO_ROOT / "predictive_maintenance_dbt"
STREAMLIT_APP_DIR = REPO_ROOT / "oee_command_center_app"
CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "snow-coco")


def statements_from_sql_file(path: pathlib.Path) -> list[str]:
    """Strip full-line comments, then split the remaining SQL text on ';' --
    tracking $$...$$ blocks (stored-procedure bodies) so a semicolon inside
    one (e.g. in a Python docstring/comment) doesn't fragment the statement.
    """
    lines = [
        line for line in path.read_text().splitlines() if not line.strip().startswith("--")
    ]
    raw = "\n".join(lines)
    statements: list[str] = []
    current: list[str] = []
    in_dollar_block = False
    i = 0
    while i < len(raw):
        if raw[i : i + 2] == "$$":
            in_dollar_block = not in_dollar_block
            current.append("$$")
            i += 2
            continue
        if raw[i] == ";" and not in_dollar_block:
            statements.append("".join(current))
            current = []
            i += 1
            continue
        current.append(raw[i])
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [s.strip() for s in statements if s.strip()]


def run_sql_file(cur, path: pathlib.Path) -> None:
    print(f"--- Running {path.relative_to(REPO_ROOT)} ---")
    for stmt in statements_from_sql_file(path):
        print(f"  {stmt.splitlines()[0][:80]}...")
        cur.execute(stmt)
        for row in cur.fetchall():
            print(f"    {row}")


def generate_full_data(seed: int, now: str, reuse_dataset_path: str | None) -> None:
    print(f"--- Generating full dataset (seed={seed}, now={now}) ---")
    from generator.full_data_generator import _reuse_dataset_exists, run_simulation

    if reuse_dataset_path and _reuse_dataset_exists(reuse_dataset_path):
        print(f"Reusing existing dataset at {reuse_dataset_path} -- skipping generation.")
        return

    now_date = date.fromisoformat(now)
    rng = np.random.default_rng(seed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tables = run_simulation(rng, now_date, str(OUTPUT_DIR))
    for name, df in tables.items():
        path = OUTPUT_DIR / f"{name}.parquet"
        # use_deprecated_int96_timestamps=True: Snowflake's Parquet COPY INTO reader
        # misinterprets the newer INT64 TIMESTAMP logical-type unit annotation --
        # the older INT96 encoding is unambiguous and what Snowflake expects.
        df.to_parquet(path, index=False, use_deprecated_int96_timestamps=True)
        print(f"Wrote {len(df)} row(s) to {path}")


def run_dbt_phase1() -> None:
    # Shells out to the bare `dbt` binary -- only on PATH inside the uv venv,
    # so this script must be invoked as `uv run python manage.py up`, not a
    # bare `python manage.py up`.
    print("--- Running dbt seed ---")
    subprocess.run(["dbt", "seed"], cwd=DBT_DIR, check=True)
    # Phase 1: everything except model-inference tables (FR-OPS-02) AND minus
    # feast__training_dataset_rul (SH-50) -- that model's own dbt run moves to
    # after 05_train_models.sql below, since it calls MODEL(isolation_forest_
    # model, ...) directly and that model doesn't exist yet at this point in
    # the sequence (docs/designs/SH-50-anomaly-into-rul-features.md §4). Seed
    # must come first -- FEAST's baseline join ref()'s the seed (SH-29).
    print(
        "--- Running dbt run --exclude tag:inference+ --exclude feast__training_dataset_rul "
        "(phase 1: features) ---"
    )
    subprocess.run(
        ["dbt", "run", "--exclude", "tag:inference+", "--exclude", "feast__training_dataset_rul"],
        cwd=DBT_DIR,
        check=True,
    )


def run_dbt_training_dataset_rul() -> None:
    # SH-50: built here, after 05_train_models.sql, because this model calls
    # MODEL(cons.isolation_forest_model, DEFAULT) directly -- the model must
    # already exist (dbt's own DAG can't enforce this since it's not a
    # dbt-managed ref()).
    print("--- Running dbt run --select feast__training_dataset_rul (post-training) ---")
    subprocess.run(["dbt", "run", "--select", "feast__training_dataset_rul"], cwd=DBT_DIR, check=True)


def run_dbt_phase2_and_test() -> None:
    # Phase 2 (FR-OPS-02b): model-inference tables. A no-op today -- no model
    # is tagged 'inference' yet (S-MODEL-3 not built) -- confirmed dbt exits
    # 0 on an empty selection, just warns.
    print("--- Running dbt run --select tag:inference+ (phase 2: inference) ---")
    subprocess.run(["dbt", "run", "--select", "tag:inference+"], cwd=DBT_DIR, check=True)
    print("--- Running dbt test ---")
    subprocess.run(["dbt", "test"], cwd=DBT_DIR, check=True)


def _put_streamlit_app_files(cur) -> None:
    # streamlit_app.py goes to the stage root; every pages/*.py goes to
    # pages/ under the stage root, preserving the multipage subpath (SH-32).
    # AUTO_COMPRESS=FALSE OVERWRITE=TRUE -- same flags `demo inject-tick`
    # already uses for its own PUT, so re-running `up` re-syncs the app code.
    main_file = STREAMLIT_APP_DIR / "streamlit_app.py"
    cur.execute(
        f"PUT 'file://{main_file}' @snowcomotive.cons.streamlit_stage/ "
        "AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
    )
    for page_file in sorted((STREAMLIT_APP_DIR / "pages").glob("*.py")):
        cur.execute(
            f"PUT 'file://{page_file}' @snowcomotive.cons.streamlit_stage/pages/ "
            "AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        )


def run_post_setup() -> None:
    # SH-30/27/28/31/33: semantic view + agent + Streamlit deploy. Own
    # connector session, snowcomotive_role (owns every object this creates,
    # same reasoning as the training-procedure step above).
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "07_post_setup.sql")
        _put_streamlit_app_files(cur)
        # CREATE STREAMLIT itself isn't in 07_post_setup.sql -- it needs the
        # app files PUT to the stage first (created by that script), and PUT
        # can't run from a plain .sql file. Same "inline SQL in manage.py"
        # precedent as `demo inject-tick`'s own PUT+COPY INTO.
        print("--- Creating Streamlit app (SH-33) ---")
        cur.execute(
            "CREATE OR REPLACE STREAMLIT snowcomotive.cons.oee_command_center "
            "ROOT_LOCATION = '@snowcomotive.cons.streamlit_stage' "
            "MAIN_FILE = 'streamlit_app.py' "
            "QUERY_WAREHOUSE = snowcomotive_wh"
        )
        for row in cur.fetchall():
            print(f"  {row}")
    finally:
        conn.close()


def run_up(seed: int, now: str, reuse_dataset_path: str | None) -> None:
    # role='ACCOUNTADMIN' overrides the snow-coco connection's default login
    # role (SNOWCOMOTIVE_ROLE itself) -- required because 01_setup.sql creates
    # that role; logging in as a role that doesn't exist yet is a deadlock,
    # hit for real after a full manage.py down (2026-08-30).
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="ACCOUNTADMIN")
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "01_setup.sql")
        # Switch to snowcomotive_role for everything else so objects it
        # creates (RAW tables, the training stored procedure) are owned by
        # snowcomotive_role -- matching dbt's own connection. Staying on
        # ACCOUNTADMIN caused a real failure: a stored procedure created as
        # ACCOUNTADMIN runs with owner's rights and lacked SELECT on
        # feast tables owned by snowcomotive_role (hit for real, SH-22).
        cur.execute("USE ROLE snowcomotive_role")
        generate_full_data(seed, now, reuse_dataset_path)
        run_sql_file(cur, SCRIPTS_DIR / "02_setup_raw_ddl.sql")
        run_sql_file(cur, SCRIPTS_DIR / "03_setup_raw_load.sql")
    finally:
        conn.close()
    run_dbt_phase1()
    # Model training (FR-OPS-02a) -- own connector session, snowcomotive_role
    # (same reasoning as above: must own the feast tables it reads).
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "05_train_models.sql")
    finally:
        conn.close()
    run_dbt_training_dataset_rul()
    # RUL model training (SH-44/S-RUL-3) -- own connector session, same
    # reasoning as the isolation-forest training step above; must run after
    # feast.training_dataset_rul is built (previous step) and before phase 2.
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        run_sql_file(cur, SCRIPTS_DIR / "06_train_rul_model.sql")
    finally:
        conn.close()
    run_dbt_phase2_and_test()
    run_post_setup()
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


def _live_tick_files() -> list[pathlib.Path]:
    return sorted(LIVE_TICKS_DIR.glob("*.parquet"))


def _read_cursor() -> str | None:
    if not CURSOR_FILE.exists():
        return None
    content = CURSOR_FILE.read_text().strip()
    return content or None


def _write_cursor(filename: str) -> None:
    CURSOR_FILE.write_text(filename)


def inject_next_tick() -> None:
    files = _live_tick_files()
    if not files:
        print(f"No live tick files found in {LIVE_TICKS_DIR}. Run `manage.py up` first.")
        raise typer.Exit(code=1)

    names = [f.name for f in files]
    cursor = _read_cursor()
    if cursor is None or cursor not in names:
        if cursor is not None:
            print(f"Cursor references unknown tick '{cursor}' -- restarting from the first file.")
        next_idx = 0
    else:
        next_idx = names.index(cursor) + 1

    if next_idx >= len(files):
        print("All ticks already injected. Run `manage.py demo reset-cursor` to restart.")
        raise typer.Exit(code=0)

    next_file = files[next_idx]
    remaining_after = len(files) - next_idx - 1

    print(f"--- Injecting tick {next_file.name} ---")
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        cur.execute(
            f"PUT 'file://{next_file}' @snowcomotive.raw.landing_stage/sensor_reading/ "
            "AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        )
        cur.execute(
            "COPY INTO snowcomotive.raw.sensor_reading "
            "FROM @snowcomotive.raw.landing_stage/sensor_reading/ "
            f"FILES = ('{next_file.name}') "
            "FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
            "ON_ERROR = 'ABORT_STATEMENT'"
        )
        rows = cur.fetchall()
        columns = [c[0] for c in cur.description]
    finally:
        conn.close()

    # Cursor update only after COPY INTO confirms success (design doc
    # invariant 4) -- a failed PUT/COPY raises before this point and leaves
    # the cursor untouched, so re-running re-attempts the same tick.
    status_idx = columns.index("status") if "status" in columns else None
    if status_idx is not None and rows and not all(r[status_idx] == "LOADED" for r in rows):
        print(f"COPY INTO did not report LOADED for all files: {rows}")
        raise typer.Exit(code=1)

    _write_cursor(next_file.name)
    print(f"Injected {next_file.name}. {remaining_after} tick(s) remaining.")


def _batch_date(filename: str) -> str:
    """'reading_2026-09-10T14-30-00.parquet' -> '2026-09-10'."""
    return filename[len("reading_") : len("reading_") + 10]


def inject_next_batch() -> None:
    files = _live_tick_files()
    if not files:
        print(f"No live tick files found in {LIVE_TICKS_DIR}. Run `manage.py up` first.")
        raise typer.Exit(code=1)

    names = [f.name for f in files]
    cursor = _read_cursor()
    if cursor is None or cursor not in names:
        if cursor is not None:
            print(f"Cursor references unknown tick '{cursor}' -- restarting from the first file.")
        next_idx = 0
    else:
        next_idx = names.index(cursor) + 1

    if next_idx >= len(files):
        print("All batches already injected. Run `manage.py demo reset-cursor` to restart.")
        raise typer.Exit(code=0)

    chunk_date = _batch_date(names[next_idx])
    chunk_files = [f for f in files[next_idx:] if _batch_date(f.name) == chunk_date]
    remaining_after = len(files) - next_idx - len(chunk_files)

    print(f"--- Injecting batch {chunk_date} ({len(chunk_files)} tick file(s)) ---")
    conn = snowflake.connector.connect(connection_name=CONNECTION_NAME, role="snowcomotive_role")
    try:
        cur = conn.cursor()
        cur.execute(
            f"PUT 'file://{LIVE_TICKS_DIR}/reading_{chunk_date}*.parquet' "
            "@snowcomotive.raw.landing_stage/sensor_reading/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
        )
        file_list = ", ".join(f"'{f.name}'" for f in chunk_files)
        cur.execute(
            "COPY INTO snowcomotive.raw.sensor_reading "
            "FROM @snowcomotive.raw.landing_stage/sensor_reading/ "
            f"FILES = ({file_list}) "
            "FILE_FORMAT = (TYPE = PARQUET) MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
            "ON_ERROR = 'ABORT_STATEMENT'"
        )
        rows = cur.fetchall()
        columns = [c[0] for c in cur.description]
    finally:
        conn.close()

    # Cursor update only after COPY INTO confirms success (design doc
    # invariant 4) -- same pattern as inject_next_tick(), extended from 1
    # file to the whole date-chunk.
    status_idx = columns.index("status") if "status" in columns else None
    if status_idx is not None and rows and not all(r[status_idx] == "LOADED" for r in rows):
        print(f"COPY INTO did not report LOADED for all files: {rows}")
        raise typer.Exit(code=1)

    _write_cursor(chunk_files[-1].name)
    print(f"Injected batch {chunk_date} ({len(chunk_files)} file(s)). {remaining_after} tick(s) remaining.")


def reset_cursor() -> None:
    if CURSOR_FILE.exists():
        CURSOR_FILE.unlink()
        print(f"Cleared {CURSOR_FILE}.")
    else:
        print(f"No cursor file at {CURSOR_FILE} -- already at the start.")


app = typer.Typer(help=__doc__, no_args_is_help=True)
demo_app = typer.Typer(help="Live-tick drip-feed demo commands (SH-41 / S-DATA-9).", no_args_is_help=True)
app.add_typer(demo_app, name="demo")


@app.command("up")
def up(
    seed: int = typer.Option(42, "--seed", help="RNG seed passed through to the full generator."),
    reuse_dataset_path: str = typer.Option(
        None,
        "--reuse-dataset-path",
        help="If set and this path already has the expected bulk output files, skip generation.",
    ),
    now: str = typer.Option(
        str(date.today()), "--now", help="YYYY-MM-DD, anchors the generator's 3yr-back/8wk-forward window."
    ),
) -> None:
    """Spin up env + generate/load the full dataset."""
    run_up(seed, now, reuse_dataset_path)


@app.command("down")
def down() -> None:
    """Tear down env (database/role/warehouse)."""
    run_down()


@demo_app.command("inject-tick")
def demo_inject_tick() -> None:
    """Inject the next output/live_ticks/ file into RAW.SENSOR_READING."""
    inject_next_tick()


@demo_app.command("inject-batch")
def demo_inject_batch() -> None:
    """Inject every output/live_ticks/ file in the next ~24h date-chunk into RAW.SENSOR_READING, in one session."""
    inject_next_batch()


@demo_app.command("reset-cursor")
def demo_reset_cursor() -> None:
    """Reset the drip-feed cursor so the next inject-tick starts from the first file."""
    reset_cursor()


if __name__ == "__main__":
    app()
