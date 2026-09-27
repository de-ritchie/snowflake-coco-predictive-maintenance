"""Prioritization page (SH-48, S-RUL-7; nav label renamed from "Priority Queue"
during manual testing): ranked table of cons.fct_priority_score + a stacked
bar chart of each machine's 4 weighted contributing factors + a "survives its
own demand?" badge. See
docs/designs/SH-48-forecast-oee-priority-queue-pages.md §3 for the frozen
design.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app import get_connection, render_sidebar, require_persona

st.set_page_config(page_title="Prioritization", layout="wide")

BADGE_STYLE = {
    "survives": ("Yes", "#2e7d32", "#e8f5e9"),
    "at-risk-of-demand": ("No", "#c62828", "#ffebee"),
}


def render_badge(status: str) -> str:
    label, fg, bg = BADGE_STYLE[status]
    return (
        f'<span style="background-color:{bg};color:{fg};padding:2px 10px;'
        f'border-radius:12px;font-weight:600;font-size:0.85em;">{label}</span>'
    )


@st.cache_data(ttl=300)
def load_priority_queue() -> pd.DataFrame:
    conn = get_connection()
    return conn.query(
        """
        SELECT
            eq.equipment_id,
            eq.equipment_name,
            eq.line_name,
            ps.priority_score,
            ps.rul_urgency,
            ps.demand_pressure,
            ps.inventory_buffer,
            ps.spare_part_readiness,
            ps.predicted_rul_hours,
            ps.required_run_hours_next_4wk
        FROM cons.cons__fct_priority_score ps
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = ps.equipment_id
        ORDER BY ps.priority_score DESC
        """,
        ttl=300,
    )


render_sidebar()
require_persona()
st.title("Prioritization")
st.caption(
    "Ranked by composite priority score (RUL urgency, demand pressure, "
    "inventory buffer, spare-part readiness) -- see docs/designs/"
    "SH-47-priority-score.md for the scoring formula."
)

df = load_priority_queue()

if df.empty:
    st.info("No priority score data found in cons.cons__fct_priority_score.")
    st.stop()

df["SURVIVES_DEMAND"] = df["PREDICTED_RUL_HOURS"] >= df["REQUIRED_RUN_HOURS_NEXT_4WK"]

st.subheader("Ranked table")
table_df = df[
    [
        "EQUIPMENT_NAME",
        "LINE_NAME",
        "PRIORITY_SCORE",
        "RUL_URGENCY",
        "DEMAND_PRESSURE",
        "INVENTORY_BUFFER",
        "SPARE_PART_READINESS",
    ]
].copy()
table_df["PRIORITY_SCORE"] = table_df["PRIORITY_SCORE"].round(1)
table_df["Survives its own demand?"] = df["SURVIVES_DEMAND"].map(
    lambda ok: "Yes" if ok else "No"
)
st.dataframe(table_df, width="stretch", hide_index=True)

st.subheader("Weighted contribution to priority score")
chart_df = df.copy()
chart_df["RUL Urgency"] = 40 * chart_df["RUL_URGENCY"]
chart_df["Demand Pressure"] = 25 * chart_df["DEMAND_PRESSURE"]
chart_df["Inventory Buffer"] = 20 * chart_df["INVENTORY_BUFFER"]
chart_df["Spare-Part Readiness"] = 15 * chart_df["SPARE_PART_READINESS"]

melted = chart_df.melt(
    id_vars=["EQUIPMENT_NAME"],
    value_vars=["RUL Urgency", "Demand Pressure", "Inventory Buffer", "Spare-Part Readiness"],
    var_name="Factor",
    value_name="Points",
)
fig = px.bar(
    melted,
    x="EQUIPMENT_NAME",
    y="Points",
    color="Factor",
    barmode="stack",
    title="Priority score contribution by factor (bar height = priority_score)",
)
fig.update_layout(xaxis_title="Machine", yaxis_title="Priority score (points)")
st.plotly_chart(fig, width="stretch")

st.subheader("Survives its own demand?")
st.caption(
    "Compares predicted_rul_hours against required_run_hours_next_4wk -- "
    "both read verbatim from cons.fct_priority_score, neither re-derived here."
)
for _, row in df.iterrows():
    status = "survives" if row["SURVIVES_DEMAND"] else "at-risk-of-demand"
    cols = st.columns([3, 2, 2, 2, 2])
    with cols[0]:
        st.markdown(f"**{row['EQUIPMENT_NAME']}** ({row['LINE_NAME']})")
    with cols[1]:
        st.markdown(render_badge(status), unsafe_allow_html=True)
    with cols[2]:
        st.caption(f"Predicted RUL: {row['PREDICTED_RUL_HOURS']:.0f}h")
    with cols[3]:
        st.caption(f"Required next 4wk: {row['REQUIRED_RUN_HOURS_NEXT_4WK']:.0f}h")
    with cols[4]:
        if st.button("View sensor detail", key=f"jump_{row['EQUIPMENT_ID']}"):
            st.session_state["jump_to_equipment"] = row["EQUIPMENT_ID"]
            st.switch_page("pages/1_Overview.py")
