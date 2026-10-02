"""Plant Overview page (SH-74): Plant Manager's home — KPI cards, asset risk
summary, and historical OEE trend.

Skipped sections (SYNTHETIC, no real data source):
- Financial Exposure (PD-C1/C3/C5 — needs cost_per_hour_of_downtime dbt var
  and revenue_per_unit seed column)
- Predicted Availability Forecast (PD-D4/D6/D7 — needs new forecasting
  derivation)
- Order demand overlay (PD-D5 — parent chart PD-D4 skipped)

See mockup_v2/DATA_MAP.md for the full element-to-query mapping.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app import get_connection, render_sidebar, require_persona

st.set_page_config(page_title="Plant Overview", layout="wide")

HEALTH_THRESHOLDS = {"healthy": 40, "watch": 60}

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


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_line_names() -> list[str]:
    """DX-A1 pattern: distinct sensor-enabled line names for the filter dropdown."""
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


def load_kpi_data(line_filter: str) -> pd.DataFrame:
    """PD-A1 through PD-A8 (Weekly): OEE, Availability, Performance, Quality with deltas."""
    conn = get_connection()
    line_clause = "" if line_filter == "All Lines" else f"AND line_name = '{line_filter}'"
    return conn.query(
        f"""
        WITH ranked AS (
            SELECT
                line_name,
                period_week,
                oee_pct,
                availability_pct,
                performance_pct,
                quality_pct,
                scheduled_hours,
                LAG(oee_pct) OVER (PARTITION BY line_name ORDER BY period_week) AS prev_oee_pct,
                LAG(availability_pct) OVER (PARTITION BY line_name ORDER BY period_week) AS prev_availability_pct
            FROM cons.cons__fct_oee
            WHERE period_week <= CURRENT_DATE()
        )
        SELECT *
        FROM ranked
        WHERE period_week = (SELECT MAX(period_week) FROM cons.cons__fct_oee WHERE period_week <= CURRENT_DATE())
          {line_clause}
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )


def load_kpi_data_monthly(line_filter: str) -> pd.DataFrame:
    """PD-A1 through PD-A8 (Monthly): month-level weighted aggregation with deltas."""
    conn = get_connection()
    line_clause = "" if line_filter == "All Lines" else f"AND line_name = '{line_filter}'"
    return conn.query(
        f"""
        WITH monthly AS (
            SELECT
                line_name,
                DATE_TRUNC('month', period_week) AS period_month,
                SUM(oee_pct * scheduled_hours) / NULLIF(SUM(scheduled_hours), 0) AS oee_pct,
                SUM(availability_pct * scheduled_hours) / NULLIF(SUM(scheduled_hours), 0) AS availability_pct,
                MAX(performance_pct) AS performance_pct,
                MAX(quality_pct) AS quality_pct,
                SUM(scheduled_hours) AS scheduled_hours
            FROM cons.cons__fct_oee
            WHERE period_week <= CURRENT_DATE()
            GROUP BY line_name, DATE_TRUNC('month', period_week)
        ),
        ranked AS (
            SELECT
                *,
                LAG(oee_pct) OVER (PARTITION BY line_name ORDER BY period_month) AS prev_oee_pct,
                LAG(availability_pct) OVER (PARTITION BY line_name ORDER BY period_month) AS prev_availability_pct
            FROM monthly
        )
        SELECT *
        FROM ranked
        WHERE period_month = (SELECT MAX(DATE_TRUNC('month', period_week)) FROM cons.cons__fct_oee WHERE period_week <= CURRENT_DATE())
          {line_clause}
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )


def load_asset_risk(line_filter: str) -> pd.DataFrame:
    """PD-B1 through PD-B6: combined asset risk query (SH-85: +forward $ at risk)."""
    conn = get_connection()
    line_clause = "" if line_filter == "All Lines" else f"AND eq.line_name = '{line_filter}'"
    return conn.query(
        f"""
        WITH latest_service AS (
            SELECT
                equipment_id,
                hours_since_last_service
            FROM cons.cons__fct_sensor_reading
            QUALIFY ROW_NUMBER() OVER (PARTITION BY equipment_id ORDER BY reading_ts DESC) = 1
        )
        SELECT
            eq.equipment_name,
            eq.line_name,
            ps.predicted_rul_hours,
            ls.hours_since_last_service,
            ps.priority_score,
            de.forward_dollar_at_risk_usd
        FROM cons.cons__fct_priority_score ps
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = ps.equipment_id
        LEFT JOIN latest_service ls
            ON ls.equipment_id = ps.equipment_id
        LEFT JOIN cons.cons__fct_dollar_exposure de
            ON de.equipment_id = ps.equipment_id
        WHERE 1=1
          {line_clause}
        ORDER BY ps.priority_score DESC
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )


def load_historical_revenue_loss() -> pd.DataFrame:
    """SH-85 §4.1: per-line historical realized $ impact (always all lines)."""
    conn = get_connection()
    return conn.query(
        """
        SELECT
            eq.line_name,
            SUM(de.historical_realized_impact_usd) AS lost_revenue
        FROM cons.cons__fct_dollar_exposure de
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = de.equipment_id
        GROUP BY eq.line_name
        """,
        ttl=0,
    )


def fmt_dollar(val: float) -> str:
    if abs(val) >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    if abs(val) >= 1_000:
        return f"${val / 1_000:.1f}k"
    return f"${val:,.0f}"


def load_oee_trend(line_filter: str) -> pd.DataFrame:
    """PD-D1/D2: historical OEE trend (3 months)."""
    conn = get_connection()
    line_clause = "" if line_filter == "All Lines" else f"AND line_name = '{line_filter}'"
    df = conn.query(
        f"""
        SELECT
            line_name,
            period_week,
            oee_pct,
            scheduled_hours
        FROM cons.cons__fct_oee
        WHERE period_week >= DATEADD(month, -3,
            (SELECT MAX(period_week) FROM cons.cons__fct_oee WHERE period_week <= CURRENT_DATE()))
          AND period_week <= CURRENT_DATE()
          {line_clause}
        ORDER BY line_name, period_week
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )
    df["PERIOD_WEEK"] = pd.to_datetime(df["PERIOD_WEEK"])
    df["OEE_PCT"] = df["OEE_PCT"] * 100
    return df


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

render_sidebar()
require_persona()
st.title("Plant Overview")

# --- Filter bar (Line + Granularity) ----------------------------------------

line_names = load_line_names()
filter_cols = st.columns(2)
with filter_cols[0]:
    line_filter = st.selectbox("Line", ["All Lines"] + line_names, key="plant_line_filter")
with filter_cols[1]:
    granularity = st.selectbox("Granularity", ["Weekly", "Monthly"], key="oee_granularity")

# --- Section A: KPI cards ---------------------------------------------------

kpi_df = load_kpi_data_monthly(line_filter) if granularity == "Monthly" else load_kpi_data(line_filter)
period_label = "month" if granularity == "Monthly" else "week"

if kpi_df.empty:
    st.info("No OEE data found.")
    st.stop()

total_sch = kpi_df["SCHEDULED_HOURS"].sum()
plant_oee = (kpi_df["OEE_PCT"] * kpi_df["SCHEDULED_HOURS"]).sum() / total_sch
prev_oee = (kpi_df["PREV_OEE_PCT"] * kpi_df["SCHEDULED_HOURS"]).sum() / total_sch
plant_avail = (kpi_df["AVAILABILITY_PCT"] * kpi_df["SCHEDULED_HOURS"]).sum() / total_sch
prev_avail = (kpi_df["PREV_AVAILABILITY_PCT"] * kpi_df["SCHEDULED_HOURS"]).sum() / total_sch
perf_pct = float(kpi_df["PERFORMANCE_PCT"].iloc[0])
qual_pct = float(kpi_df["QUALITY_PCT"].iloc[0])

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric(
        "OEE",
        f"{plant_oee * 100:.1f}%",
        delta=f"{(plant_oee - prev_oee) * 100:+.1f}pp vs. prior {period_label}",
    )
with kpi_cols[1]:
    st.metric(
        "Availability",
        f"{plant_avail * 100:.1f}%",
        delta=f"{(plant_avail - prev_avail) * 100:+.1f}pp vs. prior {period_label}",
    )
with kpi_cols[2]:
    st.metric("Performance", f"{perf_pct * 100:.1f}%", delta=f"0.0pp vs. prior {period_label}")
with kpi_cols[3]:
    st.metric("Quality", f"{qual_pct * 100:.1f}%", delta=f"0.0pp vs. prior {period_label}")

# --- Section A2: Historical Revenue Loss (SH-85 §4.4) ----------------------

st.subheader("Historical Revenue Loss")

loss_df = load_historical_revenue_loss()
if not loss_df.empty:
    overall = loss_df["LOST_REVENUE"].sum()
    lines = loss_df.set_index("LINE_NAME")["LOST_REVENUE"].to_dict()

    if line_filter == "All Lines":
        cols = st.columns(3)
        with cols[0]:
            st.metric("Overall", fmt_dollar(overall))
        for i, (name, val) in enumerate(lines.items()):
            with cols[i + 1]:
                st.metric(f"{name} Line", fmt_dollar(val))
    else:
        cols = st.columns(2)
        with cols[0]:
            st.metric("Overall (plant-wide)", fmt_dollar(overall))
        with cols[1]:
            st.metric(f"{line_filter} Line", fmt_dollar(lines.get(line_filter, 0)))

# --- Section B: Asset Risk Summary ------------------------------------------

st.subheader("Asset Risk Summary")

asset_df = load_asset_risk(line_filter)
if not asset_df.empty:
    for _, row in asset_df.iterrows():
        status = health_status(float(row["PRIORITY_SCORE"]))
        with st.container(border=True):
            c1, c_dollar, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2, 2])
            with c1:
                st.markdown(f"**{row['EQUIPMENT_NAME']}**")
                st.caption(f"{row['LINE_NAME']} line")
            with c_dollar:
                dar = row["FORWARD_DOLLAR_AT_RISK_USD"]
                st.metric("$ at Risk", fmt_dollar(dar) if pd.notna(dar) else "$0")
            with c2:
                st.metric("RUL", f"{row['PREDICTED_RUL_HOURS']:.0f} hrs")
            with c3:
                hrs = row["HOURS_SINCE_LAST_SERVICE"]
                st.metric("Since service", f"{hrs:.0f} hrs" if pd.notna(hrs) else "N/A")
            with c4:
                st.metric("Priority", f"{row['PRIORITY_SCORE']:.1f}")
            with c5:
                st.markdown(render_badge(status), unsafe_allow_html=True)

# --- Section D: Historical OEE Trend ----------------------------------------

st.subheader("Historical OEE")

oee_df = load_oee_trend(line_filter)
if oee_df.empty:
    st.write("No OEE trend data available.")
else:
    if granularity == "Monthly":
        plot_df = oee_df.copy()
        plot_df["PERIOD_MONTH"] = plot_df["PERIOD_WEEK"].dt.to_period("M").dt.to_timestamp()
        plot_df = (
            plot_df.groupby(["LINE_NAME", "PERIOD_MONTH"], as_index=False)
            .agg(OEE_PCT=("OEE_PCT", "mean"))
        )
        fig = px.line(plot_df, x="PERIOD_MONTH", y="OEE_PCT", color="LINE_NAME",
                      labels={"OEE_PCT": "OEE %", "PERIOD_MONTH": "Month", "LINE_NAME": "Line"},
                      markers=True)
    else:
        fig = px.line(oee_df, x="PERIOD_WEEK", y="OEE_PCT", color="LINE_NAME",
                      labels={"OEE_PCT": "OEE %", "PERIOD_WEEK": "Week", "LINE_NAME": "Line"},
                      markers=True)

    fig.update_layout(yaxis_title="OEE %")
    st.plotly_chart(fig, use_container_width=True)
