"""Overview page (SH-21, S-APP-1 T2): health status cards + per-machine
sensor detail. See docs/designs/SH-21-streamlit-shell-overview-page.md for
the frozen design (badge logic, sensor-detail exception to Consumption-only
sourcing, etc.).
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from streamlit_app import get_connection, render_sidebar

# Streamlit's classic multipage convention runs each page script
# independently -- layout must be (re-)requested per script, or a direct
# reload/URL load of this page falls back to the "centered" default even
# though streamlit_app.py already requested "wide" (client-side nav from
# the home page carries that setting over without a full remount, which is
# why the layout looked different between reload vs. in-app navigation).
st.set_page_config(page_title="Overview", layout="wide")

SENSOR_TYPES = ["VIBRATION", "TEMPERATURE", "RPM"]

BADGE_STYLE = {
    "healthy": ("Healthy", "#2e7d32", "#e8f5e9"),
    "watch": ("Watch", "#b26a00", "#fff3e0"),
    "at-risk": ("At Risk", "#c62828", "#ffebee"),
}


def render_badge(status: str) -> str:
    label, fg, bg = BADGE_STYLE[status]
    return (
        f'<span style="background-color:{bg};color:{fg};padding:2px 10px;'
        f'border-radius:12px;font-weight:600;font-size:0.85em;">{label}</span>'
    )


@st.cache_data(ttl=300)
def load_equipment() -> pd.DataFrame:
    conn = get_connection()
    return conn.query(
        """
        SELECT equipment_id, equipment_name, line_name
        FROM cons.cons__dim_equipment
        WHERE is_sensor_enabled
        ORDER BY equipment_id
        """,
        ttl=300,
    )


@st.cache_data(ttl=300)
def load_health_status() -> pd.DataFrame:
    """Decision #1: at-risk if the latest reading is anomalous; watch if not
    latest but at least one anomaly occurred in the trailing 48h (~12
    readings at ~4h cadence) anchored on that machine's own latest reading
    (this is a fixed-window demo dataset, not live wall-clock data); else
    healthy.
    """
    conn = get_connection()
    df = conn.query(
        """
        WITH latest AS (
            SELECT equipment_id, MAX(reading_ts) AS latest_ts
            FROM cons.cons__fct_anomaly_result
            GROUP BY equipment_id
        ),
        latest_flag AS (
            SELECT a.equipment_id, a.is_anomaly AS latest_is_anomaly
            FROM cons.cons__fct_anomaly_result a
            JOIN latest l
                ON a.equipment_id = l.equipment_id
                AND a.reading_ts = l.latest_ts
        ),
        trailing_flag AS (
            SELECT a.equipment_id,
                   MAX(IFF(a.is_anomaly, 1, 0)) AS any_anomaly_48h
            FROM cons.cons__fct_anomaly_result a
            JOIN latest l ON a.equipment_id = l.equipment_id
            WHERE a.reading_ts > DATEADD(hour, -48, l.latest_ts)
              AND a.reading_ts <= l.latest_ts
            GROUP BY a.equipment_id
        )
        SELECT
            lf.equipment_id,
            lf.latest_is_anomaly,
            tf.any_anomaly_48h
        FROM latest_flag lf
        LEFT JOIN trailing_flag tf ON lf.equipment_id = tf.equipment_id
        """,
        ttl=300,
    )
    return df


def status_for(equipment_id: str, health_df: pd.DataFrame) -> str:
    row = health_df.loc[health_df["EQUIPMENT_ID"] == equipment_id]
    if row.empty:
        return "healthy"
    latest_is_anomaly = bool(row.iloc[0]["LATEST_IS_ANOMALY"])
    any_anomaly_48h = bool(row.iloc[0]["ANY_ANOMALY_48H"])
    if latest_is_anomaly:
        return "at-risk"
    if any_anomaly_48h:
        return "watch"
    return "healthy"


READINGS_PER_SENSOR = 50

@st.cache_data(ttl=300)
def load_sensor_history(equipment_id: str) -> pd.DataFrame:
    """Decision #8: sensor-detail chart reads cons.cons__fct_sensor_reading
    directly -- the named, doc-flagged exception to Consumption-only-via-
    derived-metrics sourcing (still Consumption layer, not Raw/Std/FEAST).

    Deviation from the frozen doc's "last 7 calendar days" window: real data
    has a large gap between the bulk historical load (ends 2026-01-23) and a
    single isolated demo-tick reading (2026-08-31) -- a calendar window
    anchored on the latest reading only ever captures that one lone tick.
    Using the last N readings per sensor type instead so the chart always
    shows a meaningful trend, and still surfaces an isolated tick point
    distinctly when one exists (useful for the "inject next tick" narrative).
    """
    conn = get_connection()
    df = conn.query(
        """
        SELECT reading_ts, sensor_type, reading_value
        FROM cons.cons__fct_sensor_reading
        WHERE equipment_id = ?
        ORDER BY reading_ts DESC
        LIMIT ?
        """,
        params=(equipment_id, READINGS_PER_SENSOR * len(SENSOR_TYPES)),
        ttl=300,
    )
    return df.sort_values("READING_TS")


@st.cache_data(ttl=300)
def load_priority_signals() -> pd.DataFrame:
    """T5 (SH-42): descriptive order-vs-anomaly-vs-spare-readiness context,
    NOT a computed priority score -- surfaces all of a machine's tracked
    spare parts' aggregate readiness (MIN(lead_time_days), SUM(units_on_hand)),
    never a specific part tied to a predicted failure mode (no failure-mode
    -> spare-part mapping exists in CONS). Mirrors the semantic view's
    order_driven_priority_signals verified query (scripts/07_post_setup.sql).
    """
    conn = get_connection()
    return conn.query(
        """
        WITH weekly_order AS (
            SELECT
                p.product_id, p.variant,
                o.order_week,
                o.order_units,
                AVG(o.order_units) OVER (
                    PARTITION BY o.product_id, o.variant
                    ORDER BY o.order_week
                    ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
                ) AS trailing_4wk_avg_order_units
            FROM cons.cons__fct_order o
            JOIN cons.cons__dim_product p
                ON p.product_id = o.product_id AND p.variant = o.variant
        ),
        weekly_anomaly AS (
            SELECT
                equipment_id,
                DATE_TRUNC('week', reading_ts) AS period_week,
                AVG(anomaly_score) AS avg_anomaly_score,
                SUM(IFF(is_anomaly, 1, 0)) AS anomaly_count
            FROM cons.cons__fct_anomaly_result
            GROUP BY equipment_id, DATE_TRUNC('week', reading_ts)
        ),
        spare_readiness AS (
            SELECT equipment_id, period_week, MIN(lead_time_days) AS min_lead_time_days, SUM(units_on_hand) AS total_units_on_hand
            FROM cons.cons__fct_inventory_spare
            GROUP BY equipment_id, period_week
        )
        SELECT
            m.line_name,
            m.equipment_id,
            m.equipment_name,
            wo.order_week,
            wo.order_units,
            wo.trailing_4wk_avg_order_units,
            wa.avg_anomaly_score,
            wa.anomaly_count,
            sr.min_lead_time_days,
            sr.total_units_on_hand
        FROM cons.cons__dim_equipment m
        JOIN weekly_order wo
            ON wo.product_id = m.product_id AND wo.variant = m.variant
        LEFT JOIN weekly_anomaly wa
            ON wa.equipment_id = m.equipment_id AND wa.period_week = wo.order_week
        LEFT JOIN spare_readiness sr
            ON sr.equipment_id = m.equipment_id AND sr.period_week = wo.order_week
        WHERE m.is_sensor_enabled
        ORDER BY m.line_name, wo.order_week DESC
        """,
        ttl=300,
    )


render_sidebar()
st.title("Overview")

equipment_df = load_equipment()

if equipment_df.empty:
    st.info("No sensor-enabled equipment found in cons.cons__dim_equipment.")
    st.stop()

health_df = load_health_status()

st.subheader("Machine health")
cols = st.columns(len(equipment_df))
for col, (_, eq) in zip(cols, equipment_df.iterrows()):
    status = status_for(eq["EQUIPMENT_ID"], health_df)
    with col:
        with st.container(border=True):
            st.metric(eq["EQUIPMENT_NAME"], eq["LINE_NAME"])
            st.markdown(render_badge(status), unsafe_allow_html=True)

GAP_THRESHOLD = pd.Timedelta(days=2)


def split_trend_and_latest_tick(sensor_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
    """The thin data generator's bulk historical load and its isolated demo
    "tick" reading(s) can be separated by a multi-month gap. Plotting them
    on one continuous time axis squashes the dense historical trend into a
    sliver of pixels, making a genuinely-varying series look flat. Split off
    any trailing readings more than GAP_THRESHOLD apart from the prior one
    so the trend line covers only the dense block; the latest isolated
    reading is surfaced separately instead of stretching the x-axis.
    """
    sensor_df = sensor_df.sort_values("READING_TS").reset_index(drop=True)
    if len(sensor_df) < 2:
        return sensor_df, None
    gaps = sensor_df["READING_TS"].diff()
    break_positions = gaps[gaps > GAP_THRESHOLD].index
    if break_positions.empty:
        return sensor_df, None
    split_at = break_positions[-1]
    return sensor_df.iloc[:split_at], sensor_df.iloc[split_at:].iloc[-1]


st.subheader("Sensor detail")
for _, eq in equipment_df.iterrows():
    with st.expander(f"{eq['EQUIPMENT_NAME']} ({eq['EQUIPMENT_ID']}) -- last {READINGS_PER_SENSOR} readings"):
        history_df = load_sensor_history(eq["EQUIPMENT_ID"])
        if history_df.empty:
            st.write("No sensor readings available.")
            continue
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
                st.plotly_chart(fig, width="stretch")
                if latest_tick is not None:
                    st.caption(
                        f"Latest tick: {latest_tick['READING_VALUE']:.2f} "
                        f"at {latest_tick['READING_TS']} "
                        "(isolated from trend by a data-gen gap)"
                    )

st.subheader("Order-driven priority signals")
st.caption(
    "Descriptive context only, not a computed priority score: recent order "
    "volume vs. each line's equipment anomaly trend and aggregate spare-part "
    "readiness across all tracked parts (no failure-mode-to-part mapping "
    "exists yet, so no specific part is singled out)."
)
signals_df = load_priority_signals()
if signals_df.empty:
    st.write("No order/anomaly/inventory data available.")
else:
    for line_name, line_df in signals_df.groupby("LINE_NAME"):
        latest = line_df.sort_values("ORDER_WEEK").iloc[-1]
        avg_anomaly_score = latest["AVG_ANOMALY_SCORE"]
        anomaly_count = latest["ANOMALY_COUNT"]
        min_lead_time_days = latest["MIN_LEAD_TIME_DAYS"]
        parts = []
        parts.append(f"order volume trailing-4wk avg **{latest['TRAILING_4WK_AVG_ORDER_UNITS']:.0f}** units/wk")
        if pd.notna(avg_anomaly_score):
            parts.append(f"avg anomaly score **{avg_anomaly_score:.3f}** ({int(anomaly_count)} anomalous readings)")
        else:
            parts.append("no anomaly data for the latest order week")
        if pd.notna(min_lead_time_days):
            parts.append(f"spare-part lead time as low as **{min_lead_time_days:.0f} days**")
        st.markdown(f"**{line_name}**: " + ", ".join(parts) + ".")
