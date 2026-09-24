"""SnowComotive OEE Command Center -- Streamlit app shell (SH-21, S-APP-1 T1).

Home/landing page for the native multipage app (see pages/1_Overview.py).
Owns the shared Snowflake connection helper and sidebar -- both imported by
page scripts so the sidebar renders consistently across pages.

No persona switcher yet (single agent for now, per the frozen design doc
docs/designs/SH-21-streamlit-shell-overview-page.md) and no sidebar machine
filter (only 1 sensor-enabled machine exists today) -- both explicitly out
of scope for this story.
"""

import os

import streamlit as st
from streamlit.connections import SnowflakeConnection

CONNECTION_NAME = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "snow-coco")


def get_connection() -> SnowflakeConnection:
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
    """
    conn = st.connection(CONNECTION_NAME, type="snowflake")
    cur = conn.cursor()
    try:
        cur.execute("USE ROLE SNOWCOMOTIVE_ROLE")
        cur.execute("USE WAREHOUSE SNOWCOMOTIVE_WH")
        cur.execute("USE DATABASE SNOWCOMOTIVE")
        cur.execute("USE SCHEMA RAW")
    finally:
        cur.close()
    return conn


def render_sidebar() -> None:
    """Sidebar contents for SH-21: 'Inject next tick' button only, gated
    behind ?demo=1 (decision #11) -- hidden by default. Real tick-injection
    behavior is a separate, not-yet-wired story; this is a disabled
    placeholder that establishes the gating convention.
    """
    if st.query_params.get("demo") != "1":
        return
    st.sidebar.button(
        "Inject next tick",
        disabled=True,
        help="Tick injection is not wired yet -- placeholder for a future story.",
    )


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
