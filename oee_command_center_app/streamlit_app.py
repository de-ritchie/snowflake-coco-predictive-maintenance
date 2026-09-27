"""SnowComotive OEE Command Center -- Streamlit app shell (SH-21, S-APP-1 T1).

Home/landing page for the native multipage app (see pages/1_Overview.py).
Owns the shared Snowflake connection helper, sidebar, and persona registry/
gate (SH-59) -- all imported by page scripts so they render consistently
across pages. See docs/designs/SH-54-55-56-59-60-52-persona-suite.md §5 for
the persona picker/gate design.

No sidebar machine filter yet (only 1 sensor-enabled machine exists today)
-- out of scope for this story.
"""

import os
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:
    # Type-hint only -- Streamlit-in-Snowflake's bundled streamlit build does
    # not export SnowflakeConnection from streamlit.connections (confirmed
    # live, 2026-09-28: "cannot import name 'SnowflakeConnection' from
    # 'streamlit.connections'"), even though local dev's streamlit does.
    # Guarding the import under TYPE_CHECKING means it never actually
    # executes at runtime, in either environment -- get_connection()'s
    # return annotation below is a string/forward-reference for the same
    # reason.
    from streamlit.connections import SnowflakeConnection

CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "snow-coco")

# Persona registry (SH-59, S-PERSONA-3): single source of truth for both the
# picker page and the Chat page's agent-name resolution. See
# docs/designs/SH-54-55-56-59-60-52-persona-suite.md §5.2.
PERSONAS = {
    "supervisor": {
        "label": "Maintenance Supervisor",
        "agent_name": "maintenance_supervisor_agent",
        "blurb": "Hands-on machine health, root-cause, and maintenance dispatch.",
    },
    "planner": {
        "label": "Production Planner",
        "agent_name": "production_planner_agent",
        "blurb": "Demand, inventory, and OEE risk -- escalates maintenance requests, doesn't dispatch directly.",
    },
    "plant_manager": {
        "label": "Plant Manager",
        "agent_name": "plant_manager_agent",
        "blurb": "Read-only OEE/health/inventory rollup -- no ticketing.",
    },
}


def get_connection() -> "SnowflakeConnection":
    """This project's standard Snowflake connection convention (AGENTS.md):
    the named `snow-coco` connection from ~/.snowflake/connections.toml,
    overridable via the SNOWFLAKE_CONNECTION_NAME env var (same convention
    as manage.py's CONNECTION_NAME) to target a different environment/account
    without touching code.

    Note: `st.connection(CONNECTION_NAME, type="snowflake")` -- not
    `st.connection("snowflake", connection_name=CONNECTION_NAME)` as the
    design doc's example literally wrote it -- since Streamlit treats the
    first positional arg as the connection name itself; passing "snowflake"
    there and connection_name as a kwarg raises "got multiple values for
    keyword argument 'connection_name'" (confirmed against streamlit==1.62).

    database/schema/warehouse/role are set explicitly via `USE ...` statements
    right after connecting (matching manage.py's connector-session defaults)
    rather than relying on the named connection's TOML entry to define them --
    a bare connections.toml entry (account/user/authenticator/password only,
    no defaults) otherwise leaves the session with no current database, which
    fails every query with "This session does not have a current database."

    Deliberately NOT passed as kwargs to `st.connection()` itself: Streamlit's
    SnowflakeConnection._connect() only auto-applies `connection_name` when no
    other kwargs are given; the moment any extra kwarg (database/schema/...)
    is passed, it falls through to a raw `snowflake.connector.connect(**kwargs)`
    that drops the named-connection lookup entirely -- and re-adding
    `connection_name` as an explicit kwarg collides with the positional arg
    above ("got multiple values for keyword argument 'connection_name'", the
    same conflict SH-21 already hit once with `type=`). Running `USE ...`
    after connecting avoids both problems.

    `USE ...` statements are skipped entirely when running inside actual
    Streamlit-in-Snowflake (confirmed live, 2026-09-28: "Unsupported statement
    type 'USE'" -- the owner's-rights sandboxed execution environment doesn't
    support USE ROLE/WAREHOUSE/DATABASE/SCHEMA via a raw cursor at all, a hard
    platform restriction, not a version gap). They're also unnecessary there:
    the STREAMLIT object itself lives in SNOWCOMOTIVE.CONS with
    QUERY_WAREHOUSE = snowcomotive_wh and owner SNOWCOMOTIVE_ROLE, so the
    session's role/warehouse/default-database are already correct by
    ownership, and every query in this app schema-qualifies its tables (e.g.
    `cons.cons__dim_equipment`), relying only on default database (already
    SNOWCOMOTIVE) -- not on `USE SCHEMA`. Detected the same way `Chat.py`'s
    `call_agent()` detects SiS vs. local dev: `_snowflake` is only importable
    when actually running inside Streamlit-in-Snowflake.
    """
    conn = st.connection(CONNECTION_NAME, type="snowflake")
    try:
        import _snowflake  # noqa: F401

        in_sis = True
    except ImportError:
        in_sis = False

    if not in_sis:
        cur = conn.cursor()
        try:
            cur.execute("USE ROLE SNOWCOMOTIVE_ROLE")
            cur.execute("USE WAREHOUSE SNOWCOMOTIVE_WH")
            cur.execute("USE DATABASE SNOWCOMOTIVE")
            cur.execute("USE SCHEMA RAW")
        finally:
            cur.close()
    return conn


def _get_query_param(name: str) -> str | None:
    """Streamlit-in-Snowflake's bundled streamlit build predates the stable
    `st.query_params` API (confirmed live, 2026-09-28:
    "AttributeError: module 'streamlit' has no attribute 'query_params'"),
    even though local dev's streamlit has it. Falls back to the older
    `experimental_get_query_params()` API only where `query_params` is
    actually missing -- local dev's behavior is unchanged either way, since
    it always has `query_params` and takes the first branch.
    """
    if hasattr(st, "query_params"):
        return st.query_params.get(name)
    values = st.experimental_get_query_params().get(name)
    return values[0] if values else None


def _set_query_param(name: str, value: str) -> None:
    """Write-side counterpart of _get_query_param() -- see its docstring."""
    if hasattr(st, "query_params"):
        st.query_params[name] = value
    else:
        st.experimental_set_query_params(**{name: value})


def render_sidebar() -> None:
    """Sidebar contents for SH-21: 'Inject next tick' button only, gated
    behind ?demo=1 (decision #11) -- hidden by default. Real tick-injection
    behavior is a separate, not-yet-wired story; this is a disabled
    placeholder that establishes the gating convention.
    """
    if _get_query_param("demo") != "1":
        return
    st.sidebar.button(
        "Inject next tick",
        disabled=True,
        help="Tick injection is not wired yet -- placeholder for a future story.",
    )


def require_persona() -> str:
    """Call at the top of every page except the picker itself (SH-59, §5.4).
    Returns the active persona key, or halts the page with a redirect prompt
    if none is set yet -- restores from ?persona= query param first (survives
    a hard refresh), matching the ?demo=1 convention.
    """
    if "persona" not in st.session_state:
        param = _get_query_param("persona")
        if param in PERSONAS:
            st.session_state["persona"] = param
    if "persona" not in st.session_state:
        st.info("Please choose a persona to continue.")
        if st.button("Choose persona"):
            st.switch_page("pages/0_Choose_Persona.py")
        st.stop()
    return st.session_state["persona"]


# Guarded so this only runs when Streamlit executes this file directly as
# the entry-point page -- pages/1_Overview.py imports get_connection/
# render_sidebar from this module, and a plain module-level call here would
# otherwise fire as an import side effect too (and, since Python only runs
# a module's top-level code once per process, *which* page happens to
# trigger that first import becomes non-deterministic across sessions --
# this was the actual root cause of the layout/content inconsistency).
if __name__ == "__main__":
    st.set_page_config(page_title="SnowComotive OEE Command Center", layout="wide")
    render_sidebar()

    st.title("SnowComotive OEE Command Center")
    st.write(
        "Predictive maintenance & OEE command center. "
        "Use the **Overview** page in the sidebar to view machine health."
    )
