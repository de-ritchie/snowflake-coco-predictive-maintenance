"""SnowComotive OEE Command Center -- Streamlit app shell (SH-21, S-APP-1 T1).

Home/landing page for the native multipage app (see pages/1_Overview.py).
Owns the shared Snowflake connection helper and sidebar -- both imported by
page scripts so the sidebar renders consistently across pages.

No persona switcher yet (single agent for now, per the frozen design doc
docs/designs/SH-21-streamlit-shell-overview-page.md) and no sidebar machine
filter (only 1 sensor-enabled machine exists today) -- both explicitly out
of scope for this story.
"""

import streamlit as st
from streamlit.connections import SnowflakeConnection

CONNECTION_NAME = "snow-coco"


def get_connection() -> SnowflakeConnection:
    """This project's standard Snowflake connection convention (AGENTS.md):
    the named `snow-coco` connection from ~/.snowflake/connections.toml.

    Note: `st.connection(CONNECTION_NAME, type="snowflake")` -- not
    `st.connection("snowflake", connection_name=CONNECTION_NAME)` as the
    design doc's example literally wrote it -- since Streamlit treats the
    first positional arg as the connection name itself; passing "snowflake"
    there and connection_name as a kwarg raises "got multiple values for
    keyword argument 'connection_name'" (confirmed against streamlit==1.62).
    """
    return st.connection(CONNECTION_NAME, type="snowflake")


def render_sidebar() -> None:
    """Sidebar contents for SH-21: 'Inject next tick' button only, gated
    behind ?demo=1 (decision #11) -- hidden by default. Real tick-injection
    behavior is a separate, not-yet-wired story; this is a disabled
    placeholder that establishes the gating convention.
    """
    if st.query_params.get("demo") != "1":
        return
    st.sidebar.divider()
    st.sidebar.button(
        "Inject next tick",
        disabled=True,
        help="Tick injection is not wired yet -- placeholder for a future story.",
    )


st.set_page_config(page_title="SnowComotive OEE Command Center", layout="wide")
render_sidebar()

st.title("SnowComotive OEE Command Center")
st.write(
    "Predictive maintenance & OEE command center. "
    "Use the **Overview** page in the sidebar to view machine health."
)
