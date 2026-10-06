"""Production Planning page (SH-74): Production Planner's home — order trend,
future orders, production batch gap, and machine health impact on delivery.

See mockup_v2/DATA_MAP.md §2 for the full element-to-query mapping.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from Home import get_connection, render_sidebar, require_persona

st.set_page_config(page_title="Production Planning", layout="wide")

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

def load_line_options() -> list[str]:
    """Distinct line names for the selector (same pattern as Risk & Diagnostics)."""
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


def load_order_trend() -> pd.DataFrame:
    """PR-A1 + PR-A2: historical order volume with 4-week rolling avg."""
    conn = get_connection()
    df = conn.query(
        """
        WITH weekly AS (
            SELECT
                eq.line_name,
                o.order_week,
                SUM(o.order_units) AS weekly_order_units
            FROM cons.cons__fct_order o
            JOIN cons.cons__dim_equipment eq
                ON eq.product_id = o.product_id
                AND eq.variant = o.variant
            WHERE o.order_week >= DATEADD(week, -11, DATE_TRUNC('week', CURRENT_DATE))
              AND o.order_week <= DATE_TRUNC('week', CURRENT_DATE)
            GROUP BY eq.line_name, o.order_week
        )
        SELECT
            line_name,
            order_week,
            weekly_order_units,
            AVG(weekly_order_units) OVER (
                PARTITION BY line_name
                ORDER BY order_week
                ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
            ) AS rolling_4wk_avg
        FROM weekly
        ORDER BY line_name, order_week
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )
    df["ORDER_WEEK"] = pd.to_datetime(df["ORDER_WEEK"])
    return df


def load_future_orders() -> pd.DataFrame:
    """PR-B1: future weekly order volume per line, next 12 weeks (mirrors the
    trailing-12-week window of load_order_trend() for direct comparison)."""
    conn = get_connection()
    df = conn.query(
        """
        SELECT
            eq.line_name,
            o.order_week,
            SUM(o.order_units) AS weekly_order_units
        FROM cons.cons__fct_order o
        JOIN cons.cons__dim_equipment eq
            ON eq.product_id = o.product_id
            AND eq.variant = o.variant
        WHERE o.order_week > DATE_TRUNC('week', CURRENT_DATE)
          AND o.order_week <= DATEADD(week, 12, DATE_TRUNC('week', CURRENT_DATE))
        GROUP BY eq.line_name, o.order_week
        ORDER BY eq.line_name, o.order_week
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )
    df["ORDER_WEEK"] = pd.to_datetime(df["ORDER_WEEK"])
    return df


def load_batch_gap() -> pd.DataFrame:
    """PR-C1 through PR-C5: production batch gap."""
    conn = get_connection()
    return conn.query(
        """
        SELECT
            eq.equipment_name,
            eq.line_name,
            ps.predicted_rul_hours,
            ps.required_run_hours_next_4wk,
            ps.predicted_rul_hours - ps.required_run_hours_next_4wk AS gap
        FROM cons.cons__fct_priority_score ps
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = ps.equipment_id
        ORDER BY gap ASC
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )


def load_health_impact() -> pd.DataFrame:
    """PR-D1 through PR-D5: machine health impact on delivery."""
    conn = get_connection()
    return conn.query(
        """
        SELECT
            eq.equipment_name,
            eq.line_name,
            ps.demand_pressure,
            ps.predicted_rul_hours,
            eq.throughput_units_per_hour,
            ps.priority_score
        FROM cons.cons__fct_priority_score ps
        JOIN cons.cons__dim_equipment eq
            ON eq.equipment_id = ps.equipment_id
        ORDER BY ps.priority_score DESC
        """,
        ttl=0,  # real-time: no caching (SH-75 follow-up)
    )


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------

render_sidebar()
require_persona()
st.title("Production Planning")
st.caption("Production Planner — demand trends, capacity gaps, and machine "
           "health impact on delivery")

# --- Line Selector ------------------------------------------------------------

line_options = load_line_options()
line_filter = st.selectbox(
    "Production Line",
    ["All Lines"] + line_options,
    key="pp_line_filter",
)
selected_line = None if line_filter == "All Lines" else line_filter

# === Capacity & Health =======================================================

st.subheader("Capacity & Health")
col_gap, col_health = st.columns(2)

# --- Production Batch Gap (left column) --------------------------------------

with col_gap:
    st.markdown("**Predicted Production Batch Gap**")
    st.caption("Predicted RUL minus required run-hours (next 4wk). "
               "Positive = surplus, negative = shortfall.")

    gap_df = load_batch_gap()
    if selected_line:
        gap_df = gap_df[gap_df["LINE_NAME"] == selected_line]
    if gap_df.empty:
        st.write("No batch gap data available.")
    else:
        gap_df = gap_df.copy()
        gap_df["LABEL"] = (
            gap_df["EQUIPMENT_NAME"] + "<br>" + gap_df["LINE_NAME"]
        )

        def gap_color(row):
            if row["GAP"] < 0:
                return "red"
            req = row["REQUIRED_RUN_HOURS_NEXT_4WK"]
            if req > 0 and row["GAP"] / req < 0.5:
                return "#b26a00"
            return "green"

        gap_df["COLOR"] = gap_df.apply(gap_color, axis=1)

        # Symmetric range around zero so shortfall (left) and surplus (right)
        # get equal visual weight, SHAP-plot style.
        max_abs = gap_df["GAP"].abs().max() or 1
        x_span = max_abs * 1.45

        # Sort descending so the lowest (most negative / worst shortfall)
        # renders first, at the top — SHAP plots read top-to-bottom by
        # magnitude, and Plotly places the first trace-order category at
        # the bottom of a horizontal bar chart.
        gap_df = gap_df.sort_values("GAP", ascending=False)

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=gap_df["LABEL"],
            x=gap_df["GAP"],
            orientation="h",
            marker_color=gap_df["COLOR"],
            text=gap_df["GAP"].apply(lambda v: f"{v:+.0f}h"),
            textposition="outside",
            textfont=dict(size=15),
            showlegend=False,
            hovertemplate="%{y}: %{x:+.0f}h<extra></extra>",
        ))

        fig.add_vline(x=0, line_width=1.5, line_color="gray")

        fig.add_annotation(
            text="◀ Shortfall", xref="paper", yref="paper",
            x=0.0, y=-0.28, xanchor="left", showarrow=False,
            font=dict(size=13, color="gray"),
        )
        fig.add_annotation(
            text="Surplus ▶", xref="paper", yref="paper",
            x=1.0, y=-0.28, xanchor="right", showarrow=False,
            font=dict(size=13, color="gray"),
        )

        fig.update_layout(
            xaxis=dict(
                title="Gap (hours)", range=[-x_span, x_span],
                zeroline=False, showgrid=True, gridcolor="rgba(128,128,128,0.2)",
                tickfont=dict(size=13), title_font=dict(size=14),
            ),
            # automargin lets Plotly expand the left margin to fit the
            # (often multi-line) category labels instead of letting them
            # collide with the outside-positioned bar value text.
            yaxis=dict(title="", tickfont=dict(size=14), automargin=True),
            height=max(300, len(gap_df) * 80),
            margin=dict(b=95, l=10, r=10, t=30),
        )
        fig.update_traces(cliponaxis=False)
        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "Predicted failure week is directional, not calibrated (concordance "
            "index 0.94, median absolute error ~182h on 5 test rows). Gap values "
            "should be treated as indicative, not precise."
        )

# --- Machine Health Impact (right column) ------------------------------------

with col_health:
    st.markdown("**Predicted Machine Health Impact on Delivery**")
    st.caption("Sorted by priority score — machines to watch for delivery risk.")

    impact_df = load_health_impact()
    if selected_line:
        impact_df = impact_df[impact_df["LINE_NAME"] == selected_line]
    if impact_df.empty:
        st.write("No health impact data available.")
    else:
        st.markdown(
            """
            <style>
            .st-key-health_impact_card hr {
                margin: 4px 0 !important;
            }
            .st-key-health_impact_card [data-testid="stMarkdownContainer"] p,
            .st-key-health_impact_card [data-testid="stMarkdownContainer"] h4 {
                font-size: 1.05rem !important;
            }
            .st-key-health_impact_card,
            .st-key-health_impact_card [data-testid="stHorizontalBlock"],
            .st-key-health_impact_card [data-testid="stVerticalBlock"] {
                height: auto !important;
                flex: 0 0 auto !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        with st.container(border=True, key="health_impact_card"):
            # Header row
            h1, h2, h3, h4, h5 = st.columns([3, 2, 2, 2, 2])
            with h1:
                st.markdown("**Equipment**")
            with h2:
                st.caption("Demand press.")
            with h3:
                st.caption("RUL")
            with h4:
                st.caption("Throughput")
            with h5:
                pass  # no header for badge column

            for idx, (_, row) in enumerate(impact_df.iterrows()):
                if idx > 0:
                    st.divider()
                status = health_status(float(row["PRIORITY_SCORE"]))
                c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
                with c1:
                    st.markdown(f"**{row['EQUIPMENT_NAME']}**")
                    st.caption(f"{row['LINE_NAME']} line")
                with c2:
                    st.markdown(f"#### {row['DEMAND_PRESSURE']:.0f}")
                with c3:
                    st.markdown(f"#### {row['PREDICTED_RUL_HOURS']:.0f} hrs")
                with c4:
                    st.markdown(
                        f"#### {row['THROUGHPUT_UNITS_PER_HOUR']:.0f}/hr"
                    )
                with c5:
                    st.markdown(render_badge(status), unsafe_allow_html=True)

# === Demand ===================================================================

order_df = load_order_trend()
if selected_line:
    order_df = order_df[order_df["LINE_NAME"] == selected_line]

future_df = load_future_orders()
if selected_line:
    future_df = future_df[future_df["LINE_NAME"] == selected_line]

st.subheader("Demand - Historical & Future")

if order_df.empty and future_df.empty:
    st.write("No order data available.")
else:
    fig = go.Figure()
    all_lines = sorted(
        set(order_df["LINE_NAME"]) | set(future_df["LINE_NAME"])
    )
    for line_name in all_lines:
        actual = order_df[order_df["LINE_NAME"] == line_name].sort_values("ORDER_WEEK")
        forecast = future_df[future_df["LINE_NAME"] == line_name].sort_values("ORDER_WEEK")

        if not actual.empty:
            fig.add_trace(go.Scatter(
                x=actual["ORDER_WEEK"], y=actual["WEEKLY_ORDER_UNITS"],
                name=f"{line_name} — actual", mode="lines+markers",
                legendgroup=line_name,
            ))

        # Bridge the gap: prepend the last actual point to the forecast trace
        # so the dashed segment connects continuously to the solid segment
        # instead of leaving a visual gap at the actual/forecast boundary.
        if not forecast.empty:
            if not actual.empty:
                bridge = actual.iloc[[-1]][["ORDER_WEEK", "WEEKLY_ORDER_UNITS"]]
                forecast_plot = pd.concat([bridge, forecast[["ORDER_WEEK", "WEEKLY_ORDER_UNITS"]]],
                                           ignore_index=True)
            else:
                forecast_plot = forecast
            fig.add_trace(go.Scatter(
                x=forecast_plot["ORDER_WEEK"], y=forecast_plot["WEEKLY_ORDER_UNITS"],
                name=f"{line_name} — forecast", mode="lines+markers",
                line=dict(dash="dash"),
                legendgroup=line_name,
            ))

    today_ts = pd.Timestamp.now().normalize()
    fig.add_vline(x=today_ts, line_width=1.5, line_dash="dot", line_color="gray")
    fig.add_annotation(
        text="Today", xref="x", yref="paper", x=today_ts, y=1.05,
        showarrow=False, font=dict(size=12, color="gray"),
    )

    fig.update_layout(
        xaxis=dict(title="Week", automargin=True),
        yaxis_title="Order Units",
        legend=dict(
            orientation="h", yanchor="top", y=-0.3,
            xanchor="center", x=0.5,
        ),
        margin=dict(b=140),
        height=500,
    )
    st.plotly_chart(fig, use_container_width=True)
