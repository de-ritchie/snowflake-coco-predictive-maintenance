"""Forecast OEE page (SH-48, S-RUL-7): 8-week-lookahead projected-Availability
chart translating cons.fct_priority_score.predicted_rul_hours into a calendar
failure week, shaded against cons.fct_order's demand peaks. See
docs/designs/SH-48-forecast-oee-priority-queue-pages.md §4 for the frozen
design (including the exact honesty-caveat caption text, §4.6).
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_app import get_connection, render_sidebar, require_persona

st.set_page_config(page_title="Forecast OEE", layout="wide")

LOOKAHEAD_WEEKS = 8


@st.cache_data(ttl=300)
def load_assumed_downtime_hours() -> float:
    conn = get_connection()
    df = conn.query(
        """
        SELECT MEDIAN(duration_hours) AS assumed_downtime_hours
        FROM cons.cons__fct_maintenance_event
        WHERE event_type = 'BREAKDOWN'
        """,
        ttl=300,
    )
    return float(df["ASSUMED_DOWNTIME_HOURS"].iloc[0])


@st.cache_data(ttl=300)
def load_priority_score() -> pd.DataFrame:
    conn = get_connection()
    df = conn.query(
        """
        SELECT
            ps.equipment_id,
            eq.equipment_name,
            eq.line_name,
            ps.score_ts,
            ps.predicted_rul_hours,
            DATE_TRUNC('week', DATEADD('hour', ps.predicted_rul_hours, ps.score_ts)) AS predicted_failure_week
        FROM cons.cons__fct_priority_score ps
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = ps.equipment_id
        """,
        ttl=300,
    )
    df["PREDICTED_FAILURE_WEEK"] = pd.to_datetime(df["PREDICTED_FAILURE_WEEK"])
    return df


@st.cache_data(ttl=300)
def load_week_spine_and_demand_peak() -> pd.DataFrame:
    conn = get_connection()
    df = conn.query(
        f"""
        WITH week_spine AS (
            SELECT DATEADD('week', SEQ4(), DATE_TRUNC('week', (SELECT MAX(score_ts) FROM cons.cons__fct_priority_score))) AS forecast_week
            FROM TABLE(GENERATOR(ROWCOUNT => {LOOKAHEAD_WEEKS}))
        ),
        demand_by_line_week AS (
            SELECT
                eq.line_name,
                o.order_week,
                SUM(o.order_units) AS line_order_units
            FROM cons.cons__fct_order o
            JOIN cons.cons__dim_equipment eq
                ON eq.product_id = o.product_id AND eq.variant = o.variant
            WHERE eq.is_sensor_enabled
            GROUP BY eq.line_name, o.order_week
        ),
        demand_peak AS (
            SELECT
                line_name,
                order_week,
                line_order_units,
                line_order_units = MAX(line_order_units) OVER (PARTITION BY line_name) AS is_demand_peak_week
            FROM demand_by_line_week
        )
        SELECT
            ws.forecast_week,
            dp.line_name,
            dp.line_order_units,
            dp.is_demand_peak_week
        FROM week_spine ws
        LEFT JOIN demand_peak dp
            ON dp.order_week = ws.forecast_week
        ORDER BY dp.line_name, ws.forecast_week
        """,
        ttl=300,
    )
    df["FORECAST_WEEK"] = pd.to_datetime(df["FORECAST_WEEK"])
    return df


@st.cache_data(ttl=300)
def load_latest_oee() -> pd.DataFrame:
    """cons.fct_oee.availability_pct is stored as a 0-1 ratio (1 -
    breakdown_hours/scheduled_hours, per predictive_maintenance_dbt/models/
    consumption/cons__fct_oee.sql) despite its '_pct' name -- multiplied by
    100 here so it's on the same 0-100 percentage-point scale the §4.5
    dip formula (`100 * assumed_downtime_hours / scheduled_hours`) expects.
    """
    conn = get_connection()
    df = conn.query(
        """
        SELECT line_name, period_week, scheduled_hours, availability_pct
        FROM cons.cons__fct_oee
        QUALIFY ROW_NUMBER() OVER (PARTITION BY line_name ORDER BY period_week DESC) = 1
        """,
        ttl=300,
    )
    df["AVAILABILITY_PCT"] = df["AVAILABILITY_PCT"] * 100
    return df


render_sidebar()
require_persona()
st.title("Forecast OEE")
st.caption(
    f"{LOOKAHEAD_WEEKS}-week-lookahead projected Availability, shaded where a "
    "flagged machine's predicted failure window overlaps a demand peak for "
    "its production line."
)

assumed_downtime_hours = load_assumed_downtime_hours()
priority_df = load_priority_score()
spine_df = load_week_spine_and_demand_peak()
oee_df = load_latest_oee()

if priority_df.empty or spine_df.empty or oee_df.empty:
    st.info("Insufficient data to build the forecast.")
    st.stop()

risk_window_days = st.slider(
    "Risk window (± days around predicted failure)",
    min_value=1,
    max_value=14,
    value=7,
)

# Which machine/line is "at risk" in which forecast week.
at_risk_rows = []
for _, ps_row in priority_df.iterrows():
    line_weeks = spine_df[spine_df["LINE_NAME"] == ps_row["LINE_NAME"]].copy()
    line_weeks["AT_RISK_WEEK"] = line_weeks["FORECAST_WEEK"].between(
        ps_row["PREDICTED_FAILURE_WEEK"] - pd.Timedelta(days=risk_window_days),
        ps_row["PREDICTED_FAILURE_WEEK"] + pd.Timedelta(days=risk_window_days),
    )
    line_weeks["EQUIPMENT_NAME"] = ps_row["EQUIPMENT_NAME"]
    at_risk_rows.append(line_weeks)
at_risk_df = pd.concat(at_risk_rows, ignore_index=True)

# A line-week is flagged if any of its machines is both at_risk and the week
# is a demand peak for that line.
at_risk_df["IS_DEMAND_PEAK_WEEK"] = at_risk_df["IS_DEMAND_PEAK_WEEK"].fillna(False)
flagged = (
    at_risk_df.groupby(["LINE_NAME", "FORECAST_WEEK"])
    .agg(flagged=("AT_RISK_WEEK", "any"), is_demand_peak_week=("IS_DEMAND_PEAK_WEEK", "any"))
    .reset_index()
)
flagged["FLAGGED_AND_PEAK"] = flagged["flagged"] & flagged["is_demand_peak_week"]

oee_by_line = oee_df.set_index("LINE_NAME")

fig = go.Figure()
for line_name, line_flags in flagged.groupby("LINE_NAME"):
    if line_name not in oee_by_line.index:
        continue
    baseline_availability_pct = float(oee_by_line.loc[line_name, "AVAILABILITY_PCT"])
    scheduled_hours = float(oee_by_line.loc[line_name, "SCHEDULED_HOURS"])
    line_flags = line_flags.sort_values("FORECAST_WEEK")

    projected_pct = []
    for _, wk in line_flags.iterrows():
        pct = baseline_availability_pct
        if wk["FLAGGED_AND_PEAK"]:
            pct -= 100 * assumed_downtime_hours / scheduled_hours
        elif wk["flagged"]:
            pct -= 0.5 * 100 * assumed_downtime_hours / scheduled_hours
        projected_pct.append(pct)
    line_flags = line_flags.assign(PROJECTED_AVAILABILITY_PCT=projected_pct)

    fig.add_trace(
        go.Scatter(
            x=line_flags["FORECAST_WEEK"],
            y=[baseline_availability_pct] * len(line_flags),
            mode="lines",
            name=f"{line_name} (baseline)",
            line=dict(dash="dot"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=line_flags["FORECAST_WEEK"],
            y=line_flags["PROJECTED_AVAILABILITY_PCT"],
            mode="lines+markers",
            name=f"{line_name} (projected if unaddressed)",
        )
    )
    peak_weeks = line_flags[line_flags["FLAGGED_AND_PEAK"]]
    if not peak_weeks.empty:
        fig.add_trace(
            go.Scatter(
                x=peak_weeks["FORECAST_WEEK"],
                y=peak_weeks["PROJECTED_AVAILABILITY_PCT"],
                mode="markers",
                marker=dict(size=14, symbol="x", color="red"),
                name=f"{line_name} (at-risk & demand-peak overlap)",
            )
        )

fig.update_layout(
    xaxis_title="Forecast week",
    yaxis_title="Availability %",
    title="Projected Availability: baseline vs. if unaddressed",
)
st.plotly_chart(fig, width="stretch")

st.caption(
    "Predicted failure week is derived by walking cons.fct_priority_score's "
    "predicted_rul_hours forward in wall-clock time from the model's latest "
    "scoring tick -- it is not a calibrated date. SH-46's evaluation found "
    "a concordance index of 0.94 (the model correctly ranks which machines "
    "will fail sooner in the large majority of comparable test cases) but "
    "a median absolute error of ~182 hours computed on only 5 uncensored "
    "test rows -- too small a sample to treat this chart's week-level "
    "shading as a precise, calibrated date. Read it as directional risk, "
    "not a committed forecast."
)
st.caption(
    "SH-46 also found one training-set outlier: a CNC_MILLING unit whose "
    "hours_since_install exceeded anything the model was trained on, "
    "producing a ~16.6x over-prediction of remaining life. If the machine "
    "shown here is unusually old relative to the rest of the fleet, treat "
    "its projected failure week with extra skepticism for the same reason."
)
st.caption(
    f"Assumed downtime per flagged failure ({assumed_downtime_hours:.1f} hours) is "
    "the fleet-wide median of every historical BREAKDOWN event duration in "
    "cons.fct_maintenance_event, not a fixed constant -- an actual "
    "breakdown's duration may vary substantially from this figure."
)

with st.expander("Predicted failure week per machine"):
    st.dataframe(
        priority_df[["EQUIPMENT_NAME", "LINE_NAME", "PREDICTED_RUL_HOURS", "PREDICTED_FAILURE_WEEK"]],
        width="stretch",
        hide_index=True,
    )
