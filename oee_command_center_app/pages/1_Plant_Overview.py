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
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from Home import get_connection, render_sidebar, require_persona

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


def load_historical_revenue_loss(period_grain: str) -> pd.DataFrame:
    """SH-85 follow-up: per-line Lost Revenue for the current week/month, with
    a prior-period value for the delta -- same LAG() pattern as load_kpi_data/
    load_kpi_data_monthly. Uses cons__fct_oee as a calendar spine so periods
    with zero breakdowns correctly show $0 instead of being missing rows
    (cons__fct_historical_dollar_impact only has rows for actual events).
    """
    conn = get_connection()
    return conn.query(
        f"""
        WITH period_loss AS (
            SELECT
                eq.line_name,
                DATE_TRUNC('{period_grain}', hdi.event_start_ts) AS period,
                SUM(hdi.event_realized_impact_usd) AS lost_revenue
            FROM cons.cons__fct_historical_dollar_impact hdi
            JOIN cons.cons__dim_equipment eq
                ON eq.equipment_id = hdi.equipment_id
            GROUP BY 1, 2
        ),
        spine AS (
            SELECT DISTINCT line_name, DATE_TRUNC('{period_grain}', period_week) AS period
            FROM cons.cons__fct_oee
            WHERE period_week <= CURRENT_DATE()
        ),
        joined AS (
            SELECT s.line_name, s.period, COALESCE(pl.lost_revenue, 0) AS lost_revenue
            FROM spine s
            LEFT JOIN period_loss pl
                ON pl.line_name = s.line_name AND pl.period = s.period
        ),
        ranked AS (
            SELECT
                line_name,
                period,
                lost_revenue,
                LAG(lost_revenue) OVER (PARTITION BY line_name ORDER BY period) AS prev_lost_revenue
            FROM joined
        )
        SELECT *
        FROM ranked
        WHERE period = (SELECT MAX(period) FROM spine)
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


def load_lost_revenue_trend(line_filter: str) -> pd.DataFrame:
    """SH-85 follow-up: trailing-3-month weekly Lost Revenue trend per line,
    same window/line_clause pattern as load_oee_trend. Uses cons__fct_oee as a
    calendar spine so weeks with zero breakdowns correctly plot as $0.
    """
    conn = get_connection()
    line_clause = "" if line_filter == "All Lines" else f"AND line_name = '{line_filter}'"
    df = conn.query(
        f"""
        WITH period_loss AS (
            SELECT
                eq.line_name,
                DATE_TRUNC('week', hdi.event_start_ts) AS period_week,
                SUM(hdi.event_realized_impact_usd) AS lost_revenue
            FROM cons.cons__fct_historical_dollar_impact hdi
            JOIN cons.cons__dim_equipment eq
                ON eq.equipment_id = hdi.equipment_id
            GROUP BY 1, 2
        ),
        spine AS (
            SELECT DISTINCT line_name, period_week
            FROM cons.cons__fct_oee
            WHERE period_week >= DATEADD(month, -3,
                (SELECT MAX(period_week) FROM cons.cons__fct_oee WHERE period_week <= CURRENT_DATE()))
              AND period_week <= CURRENT_DATE()
              {line_clause}
        )
        SELECT s.line_name, s.period_week, COALESCE(pl.lost_revenue, 0) AS lost_revenue
        FROM spine s
        LEFT JOIN period_loss pl
            ON pl.line_name = s.line_name AND pl.period_week = s.period_week
        ORDER BY s.line_name, s.period_week
        """,
        ttl=0,
    )
    df["PERIOD_WEEK"] = pd.to_datetime(df["PERIOD_WEEK"])
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
    with st.container(border=True):
        st.metric(
            "OEE",
            f"{plant_oee * 100:.1f}%",
            delta=f"{(plant_oee - prev_oee) * 100:+.1f}pp vs. prior {period_label}",
        )
with kpi_cols[1]:
    with st.container(border=True):
        st.metric(
            "Availability",
            f"{plant_avail * 100:.1f}%",
            delta=f"{(plant_avail - prev_avail) * 100:+.1f}pp vs. prior {period_label}",
        )
with kpi_cols[2]:
    with st.container(border=True):
        st.metric("Performance", f"{perf_pct * 100:.1f}%", delta=f"0.0pp vs. prior {period_label}")
with kpi_cols[3]:
    with st.container(border=True):
        st.metric("Quality", f"{qual_pct * 100:.1f}%", delta=f"0.0pp vs. prior {period_label}")

# --- Section A2: Lost Revenue (SH-85 follow-up) -----------------------------

period_grain = "month" if granularity == "Monthly" else "week"
loss_df = load_historical_revenue_loss(period_grain)
if not loss_df.empty:
    overall = loss_df["LOST_REVENUE"].sum()
    prev_overall = loss_df["PREV_LOST_REVENUE"].fillna(0).sum()
    lines = loss_df.set_index("LINE_NAME")[["LOST_REVENUE", "PREV_LOST_REVENUE"]].to_dict("index")

    def loss_delta(current: float, prev: float) -> str:
        prev = prev or 0
        diff = current - prev
        sign = "+" if diff >= 0 else "-"
        return f"{sign}{fmt_dollar(abs(diff))} vs. prior {period_label}"

    def loss_delta_color(current: float, prev: float) -> str:
        """No change at all should read as neutral, not red/green."""
        return "off" if current == (prev or 0) else "inverse"

    line_card_label = {"Caliper": "Lost Revenue - Caliper Line", "Engine Head": "Lost Revenue - Engine Head Line"}

    if line_filter == "All Lines":
        cols = st.columns(3)
        with cols[0]:
            with st.container(border=True):
                st.metric("Overall Lost Revenue", fmt_dollar(overall), delta=loss_delta(overall, prev_overall), delta_color=loss_delta_color(overall, prev_overall))
        for i, (name, vals) in enumerate(lines.items()):
            with cols[i + 1]:
                with st.container(border=True):
                    st.metric(
                        line_card_label.get(name, f"Lost Revenue - {name} Line"),
                        fmt_dollar(vals["LOST_REVENUE"]),
                        delta=loss_delta(vals["LOST_REVENUE"], vals["PREV_LOST_REVENUE"]),
                        delta_color=loss_delta_color(vals["LOST_REVENUE"], vals["PREV_LOST_REVENUE"]),
                    )
    else:
        cols = st.columns(2)
        with cols[0]:
            with st.container(border=True):
                st.metric("Overall Lost Revenue", fmt_dollar(overall), delta=loss_delta(overall, prev_overall), delta_color=loss_delta_color(overall, prev_overall))
        with cols[1]:
            line_vals = lines.get(line_filter, {"LOST_REVENUE": 0, "PREV_LOST_REVENUE": 0})
            with st.container(border=True):
                st.metric(
                    line_card_label.get(line_filter, f"Lost Revenue - {line_filter} Line"),
                    fmt_dollar(line_vals["LOST_REVENUE"]),
                    delta=loss_delta(line_vals["LOST_REVENUE"], line_vals["PREV_LOST_REVENUE"]),
                    delta_color=loss_delta_color(line_vals["LOST_REVENUE"], line_vals["PREV_LOST_REVENUE"]),
                )

# --- Section B: Asset Risk Summary ------------------------------------------

st.subheader("Predicted Asset Risk Summary")

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
                st.metric("Potential $ at Risk", fmt_dollar(dar) if pd.notna(dar) else "$0")
            with c2:
                st.metric("RUL", f"{row['PREDICTED_RUL_HOURS']:.0f} hrs")
            with c3:
                hrs = row["HOURS_SINCE_LAST_SERVICE"]
                st.metric("Since service", f"{hrs:.0f} hrs" if pd.notna(hrs) else "N/A")
            with c4:
                st.metric("Priority", f"{row['PRIORITY_SCORE']:.1f}")
            with c5:
                st.markdown(render_badge(status), unsafe_allow_html=True)

# --- Section D: Historical OEE & Lost Revenue -------------------------------

st.subheader("Historical OEE & Lost Revenue")

oee_df = load_oee_trend(line_filter)
loss_trend_df = load_lost_revenue_trend(line_filter)

if not oee_df.empty and not loss_trend_df.empty:
    if granularity == "Monthly":
        oee_combined = oee_df.copy()
        oee_combined["PERIOD"] = oee_combined["PERIOD_WEEK"].dt.to_period("M").dt.to_timestamp()
        oee_combined = (
            oee_combined.groupby(["LINE_NAME", "PERIOD"], as_index=False).agg(OEE_PCT=("OEE_PCT", "mean"))
        )
        loss_combined = loss_trend_df.copy()
        loss_combined["PERIOD"] = loss_combined["PERIOD_WEEK"].dt.to_period("M").dt.to_timestamp()
        loss_combined = (
            loss_combined.groupby(["LINE_NAME", "PERIOD"], as_index=False).agg(LOST_REVENUE=("LOST_REVENUE", "sum"))
        )
    else:
        oee_combined = oee_df.rename(columns={"PERIOD_WEEK": "PERIOD"})
        loss_combined = loss_trend_df.rename(columns={"PERIOD_WEEK": "PERIOD"})

    line_colors = {"Caliper": "#1f77b4", "Engine Head": "#ff7f0e"}
    combo_fig = make_subplots(specs=[[{"secondary_y": True}]])
    for line in oee_combined["LINE_NAME"].unique():
        d = oee_combined[oee_combined["LINE_NAME"] == line]
        combo_fig.add_trace(
            go.Scatter(x=d["PERIOD"], y=d["OEE_PCT"], name=f"{line} OEE %", mode="lines+markers",
                       line=dict(color=line_colors.get(line), dash="solid")),
            secondary_y=False,
        )
    for line in loss_combined["LINE_NAME"].unique():
        d = loss_combined[loss_combined["LINE_NAME"] == line]
        combo_fig.add_trace(
            go.Scatter(x=d["PERIOD"], y=d["LOST_REVENUE"], name=f"{line} Lost Revenue ($)", mode="lines+markers",
                       line=dict(color=line_colors.get(line), dash="dot")),
            secondary_y=True,
        )
    combo_fig.update_yaxes(title_text="OEE %", secondary_y=False)
    combo_fig.update_yaxes(title_text="Lost Revenue ($)", secondary_y=True)
    combo_fig.update_xaxes(title_text="Month" if granularity == "Monthly" else "Week")
    st.plotly_chart(combo_fig, use_container_width=True)
