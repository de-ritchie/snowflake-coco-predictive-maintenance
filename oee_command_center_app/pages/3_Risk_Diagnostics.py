"""Risk & Diagnostics page (SH-74): Maintenance Supervisor's home — line
selector, sensor diagnostics (per machine, expandable), and priority risk
score table with stacked factor bars.

See mockup_v2/DATA_MAP.md §3 for the full element-to-query mapping.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from Home import get_connection, render_sidebar, require_persona

st.set_page_config(page_title="Risk & Diagnostics", layout="wide")

HEALTH_THRESHOLDS = {"healthy": 40, "watch": 60}
SENSOR_TYPES = ["VIBRATION", "TEMPERATURE", "RPM"]
READINGS_PER_SENSOR = 500
GAP_THRESHOLD = pd.Timedelta(days=2)
# Only a trailing run of this few points (or fewer) after the last gap is
# treated as an "isolated demo tick" -- a real batch injection produces far
# more points than this and should be drawn as its own trend segment.
ISOLATED_TICK_MAX_POINTS = 3

BADGE_STYLE = {
    "healthy": ("Healthy", "#2e7d32", "#e8f5e9"),
    "watch": ("Watch", "#b26a00", "#fff3e0"),
    "at-risk": ("At Risk", "#c62828", "#ffebee"),
}


def health_status(score: float) -> str:
    if score < HEALTH_THRESHOLDS["healthy"]:
        return "healthy"
    if score < HEALTH_THRESHOLDS["watch"]:
        return "watch"
    return "at-risk"


def render_badge(status: str) -> str:
    label, fg, bg = BADGE_STYLE[status]
    return (
        f'<span style="background-color:{bg};color:{fg};padding:2px 10px;'
        f'border-radius:12px;font-weight:600;font-size:0.85em;">{label}</span>'
    )


def split_trend_and_latest_tick(sensor_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
    """Invariant #6: separate bulk historical load from isolated demo ticks.

    Revised 2026-09-30: the previous version only ever looked at the LAST
    gap > GAP_THRESHOLD and collapsed everything after it to a single
    point. That's right for a genuinely isolated single-tick demo
    injection, but wrong when a real production gap (e.g. a machine idle
    for a long weekend) is followed by several more days of perfectly
    continuous data (a batch injection) -- that whole continuous segment
    was being silently dropped from the visible trend line, even though
    the data is there in Snowflake. Now: only a small trailing run of
    points (<= ISOLATED_TICK_MAX_POINTS) after the last gap is treated as
    an isolated demo tick; any larger trailing segment is real data and
    gets drawn as trend. Every remaining real gap just breaks the line
    (via an inserted null-value row) instead of connecting across it or
    dropping the data on either side.
    """
    sensor_df = sensor_df.sort_values("READING_TS").reset_index(drop=True)
    if len(sensor_df) < 2:
        return sensor_df, None
    gaps = sensor_df["READING_TS"].diff()
    break_positions = list(gaps[gaps > GAP_THRESHOLD].index)
    if not break_positions:
        return sensor_df, None

    latest_tick = None
    trailing_start = break_positions[-1]
    trailing = sensor_df.iloc[trailing_start:]
    if len(trailing) <= ISOLATED_TICK_MAX_POINTS:
        latest_tick = trailing.iloc[-1]
        trend_df = sensor_df.iloc[:trailing_start].copy()
        break_positions = break_positions[:-1]
    else:
        trend_df = sensor_df.copy()

    if break_positions:
        null_rows = pd.DataFrame(
            {
                "READING_TS": [
                    trend_df.loc[pos - 1, "READING_TS"]
                    + (trend_df.loc[pos, "READING_TS"] - trend_df.loc[pos - 1, "READING_TS"]) / 2
                    for pos in break_positions
                ],
                "READING_VALUE": pd.Series([float("nan")] * len(break_positions), dtype="float64"),
            }
        )
        trend_df = pd.concat([trend_df, null_rows], ignore_index=True)
        trend_df = trend_df.sort_values("READING_TS").reset_index(drop=True)

    return trend_df, latest_tick


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_line_options() -> list[str]:
    """DX-A1: distinct line names for the selector."""
    conn = get_connection()
    df = conn.query(
        """
        SELECT DISTINCT line_name
        FROM cons.cons__dim_equipment
        WHERE is_sensor_enabled
        ORDER BY line_name
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )
    return df["LINE_NAME"].tolist()


def load_priority_table() -> pd.DataFrame:
    """DX-C1 through DX-C8: full priority risk score table."""
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
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )


def load_sensor_history(equipment_id: str) -> pd.DataFrame:
    """DX-B1/B2/B3: sensor readings (invariant #2: raw values, not FEAST z-scores)."""
    conn = get_connection()
    df = conn.query(
        """
        SELECT reading_ts, sensor_type, reading_value, hours_since_last_service
        FROM cons.cons__fct_sensor_reading
        WHERE equipment_id = ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY sensor_type ORDER BY reading_ts DESC
        ) <= ?
        """,
        params=(equipment_id, READINGS_PER_SENSOR),
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )
    return df.sort_values("READING_TS")


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

render_sidebar()
require_persona()
st.title("Risk & Diagnostics")

# --- Section A: Line Selector ------------------------------------------------

line_options = load_line_options()
line_filter = st.selectbox(
    "Production Line",
    ["All Lines"] + line_options,
    key="dx_line_filter",
)
selected_line = None if line_filter == "All Lines" else line_filter

# --- Section C: Priority Risk Score (rendered before sensors for layout) -----

st.subheader("Predicted Priority Risk Score")
st.caption(
    "Ranked by composite priority score — RUL urgency (w=0.40), demand "
    "pressure (w=0.25), inventory buffer (w=0.20), spare-part readiness "
    "(w=0.15). Values read from cons.fct_priority_score; display code "
    "does NOT recompute."
)

priority_df = load_priority_table()
if priority_df.empty:
    st.info("No priority score data found.")
    st.stop()

filtered_priority = priority_df
if selected_line:
    filtered_priority = priority_df[priority_df["LINE_NAME"] == selected_line]

if not filtered_priority.empty:
    filtered_priority = filtered_priority.copy()
    filtered_priority["SURVIVES_DEMAND"] = (
        filtered_priority["PREDICTED_RUL_HOURS"] >= filtered_priority["REQUIRED_RUN_HOURS_NEXT_4WK"]
    )

    table_df = filtered_priority[
        [
            "EQUIPMENT_NAME", "LINE_NAME", "PRIORITY_SCORE",
            "RUL_URGENCY", "DEMAND_PRESSURE", "INVENTORY_BUFFER",
            "SPARE_PART_READINESS",
        ]
    ].copy()
    table_df["PRIORITY_SCORE"] = table_df["PRIORITY_SCORE"].round(1)
    table_df["Survives demand?"] = filtered_priority["SURVIVES_DEMAND"].map(
        lambda ok: "Yes" if ok else "No"
    )
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    # Stacked factor chart
    st.subheader("Predicted Weighted Contribution to Priority Score")
    chart_df = filtered_priority.copy()
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
        labels={"EQUIPMENT_NAME": "Machine", "Points": "Priority score (points)"},
    )
    st.plotly_chart(fig, use_container_width=True)

    # Survives demand badges
    st.subheader("Predicted Survives Its Own Demand?")
    st.caption(
        "Compares predicted_rul_hours against required_run_hours_next_4wk — "
        "both read from cons.fct_priority_score."
    )
    for _, row in filtered_priority.iterrows():
        status_key = "healthy" if row["SURVIVES_DEMAND"] else "at-risk"
        survives_label = "Yes" if row["SURVIVES_DEMAND"] else "No"
        cols = st.columns([3, 2, 2, 2])
        with cols[0]:
            st.markdown(f"**{row['EQUIPMENT_NAME']}** ({row['LINE_NAME']})")
        with cols[1]:
            st.markdown(render_badge(status_key), unsafe_allow_html=True)
        with cols[2]:
            st.caption(f"Predicted RUL: {row['PREDICTED_RUL_HOURS']:.0f}h")
        with cols[3]:
            st.caption(f"Required next 4wk: {row['REQUIRED_RUN_HOURS_NEXT_4WK']:.0f}h")

# --- Section B: Sensor Diagnostics ------------------------------------------

st.subheader("Sensor Diagnostics")

equipment_list = filtered_priority[["EQUIPMENT_ID", "EQUIPMENT_NAME", "LINE_NAME", "PRIORITY_SCORE"]].to_dict("records")
jump_to_equipment = st.session_state.pop("jump_to_equipment", None)

for eq in equipment_list:
    eq_status = health_status(float(eq["PRIORITY_SCORE"]))
    header = f"{render_badge(eq_status)} **{eq['EQUIPMENT_NAME']}** — {eq['LINE_NAME']} line"
    with st.expander(
        f"{eq['EQUIPMENT_NAME']} ({eq['LINE_NAME']}) — sensor detail",
        expanded=(eq["EQUIPMENT_ID"] == jump_to_equipment),
    ):
        history_df = load_sensor_history(eq["EQUIPMENT_ID"])
        if history_df.empty:
            st.write("No sensor readings available.")
            continue

        # Hours since last service, from the latest row already present in
        # the 500-reading-per-sensor window above -- never a separate query,
        # so it's only shown if that value actually falls inside the window.
        latest_row = history_df.loc[history_df["READING_TS"].idxmax()]
        service_ts = None
        if pd.notna(latest_row.get("HOURS_SINCE_LAST_SERVICE")):
            service_ts = latest_row["READING_TS"] - pd.Timedelta(
                hours=float(latest_row["HOURS_SINCE_LAST_SERVICE"])
            )
            st.caption(
                f"Hours since last service: {latest_row['HOURS_SINCE_LAST_SERVICE']:.0f}h "
                f"(as of {latest_row['READING_TS']})"
            )

        sensor_cols = st.columns(len(SENSOR_TYPES))
        for sensor_col, sensor_type in zip(sensor_cols, SENSOR_TYPES):
            sensor_df = history_df[history_df["SENSOR_TYPE"] == sensor_type]
            if sensor_df.empty:
                continue
            trend_df, latest_tick = split_trend_and_latest_tick(sensor_df)
            with sensor_col:
                fig = px.line(
                    trend_df,
                    x="READING_TS",
                    y="READING_VALUE",
                    title=sensor_type,
                )
                # Dotted "Last service" marker (Demand-page "Today" line
                # style) -- only drawn if the estimated service timestamp
                # actually falls inside this sensor's own 500-reading window.
                if service_ts is not None and not sensor_df.empty:
                    window_start = sensor_df["READING_TS"].min()
                    window_end = sensor_df["READING_TS"].max()
                    if window_start <= service_ts <= window_end:
                        fig.add_vline(
                            x=service_ts, line_width=1.5,
                            line_dash="dot", line_color="gray",
                        )
                        fig.add_annotation(
                            text="Last service", xref="x", yref="paper",
                            x=service_ts, y=1.05, showarrow=False,
                            font=dict(size=10, color="gray"),
                        )
                st.plotly_chart(
                    fig, use_container_width=True,
                    key=f"sensor_{eq['EQUIPMENT_ID']}_{sensor_type}",
                )
                if latest_tick is not None:
                    st.caption(
                        f"Latest tick: {latest_tick['READING_VALUE']:.2f} "
                        f"at {latest_tick['READING_TS']} "
                        "(isolated from trend by a data-gen gap)"
                    )
