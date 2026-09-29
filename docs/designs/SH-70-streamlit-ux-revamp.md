# SH-70 — Streamlit UI/UX Revamp Design
## SnowComotive — Predictive Maintenance & OEE Command Center

**Story**: SH-70 (Spike: Streamlit UI/UX revamp, S-STRETCH-5, parent SH-7/EPIC-STRETCH)
**Status**: Frozen (user-confirmed)

---

## 1. Overview

Consolidate the existing 5-page app (`Choose Persona`, `Overview`, `Prioritization`, `Forecast OEE`, `Chat`) into a 5-page structure organized around the 3 personas' primary workflows, with a persistent persona indicator in the sidebar and per-persona auto-routing from the landing page. Add a financial/dollar risk metric derived from OEE loss, backed by new synthetic data (`revenue_per_unit` on `dim_product` + `cost_per_hour_of_downtime` dbt var).

### Mockup deliverable

`mockup_v2/` — multi-file HTML (one file per page + shared shell/styles), visually faking Streamlit's widget look (cards, metrics, sidebar, charts). Not a React/Angular SPA; designed to look like what the real Streamlit app will render. Mock data inline, no live Snowflake connection.

---

## 2. Page Structure

| # | File | Page name | Persona home | Content summary |
|---|------|-----------|-------------|-----------------|
| 0 | `0_Choose_Persona.py` | Choose Persona | — (landing) | 3 persona cards, on-click routes to that persona's home page |
| 1 | `1_Plant_Dashboard.py` | Plant Dashboard | **Plant Manager** | Plant-wide + per-line OEE%, historical + predicted OEE, financial risk (dollar loss), asset risk summary |
| 2 | `2_Production.py` | Production | **Production Planner** | Historical & future orders, production batch gap (demand vs capacity), machine health impact on delivery |
| 3 | `3_Diagnostics.py` | Diagnostics | **Maintenance Supervisor** | Sensor charts switchable by line, priority risk score table with factor breakdown |
| 4 | `4_Chat.py` | Chat | — (common) | Persona-aware agent chat (as-is, no structural change) |

All personas can manually navigate to all pages. Selecting a persona on the landing page auto-routes:
- Plant Manager → Plant Dashboard
- Production Planner → Production
- Maintenance Supervisor → Diagnostics

---

## 3. Persona Indicator

**Location**: `st.sidebar`, visible on every page (via `render_sidebar()`).
**Content**: Text label only — e.g. `"Viewing as: Maintenance Supervisor"`. No re-selection UI in the sidebar; persona change requires navigating back to Choose Persona.
**Implementation**: Read `st.session_state["persona"]` and render via `st.sidebar.caption(...)`. Minimal change to existing `render_sidebar()`.

---

## 4. Page Designs

### 4.1 Choose Persona (page 0)

Structurally identical to current `0_Choose_Persona.py`. Only change: on persona selection, `st.switch_page()` targets the persona's home page instead of always routing to Overview.

| Persona | Routes to |
|---------|-----------|
| Plant Manager | `pages/1_Plant_Dashboard.py` |
| Production Planner | `pages/2_Production.py` |
| Maintenance Supervisor | `pages/3_Diagnostics.py` |

### 4.2 Plant Dashboard (page 1 — Plant Manager home)

This page consolidates the current Overview's OEE trend, the Forecast OEE page's predicted availability, and adds the new financial risk metric. It's the plant-manager's "one screen" answer to "how is my plant doing and what's the financial exposure."

**Section A: OEE KPI cards** (top row, `st.columns(3)`)

| Card | Source | Computation |
|------|--------|-------------|
| Plant OEE % | `cons__fct_oee` | Weighted average of `oee_pct` across lines for the latest `period_week`, weighted by `scheduled_hours` |
| Caliper OEE % | `cons__fct_oee` | `oee_pct` for `line_name = 'Caliper'`, latest `period_week` |
| Engine Head OEE % | `cons__fct_oee` | `oee_pct` for `line_name = 'Engine Head'`, latest `period_week` |

Each card rendered as `st.metric` with `delta` showing week-over-week change (current − previous week's value). Delta is informational only, not a forecast.

**Section B: Financial risk** (`st.container(border=True)`)

Two-level display:
1. **Plant-level rollup**: `Total Lost Revenue = SUM(breakdown_hours) × cost_per_hour_of_downtime` — a single headline dollar figure using the global dbt var (see §5).
2. **Per-line detail** (expandable or below): `Line Lost Revenue = SUM(breakdown_hours × throughput_units_per_hour × revenue_per_unit)` per line, for the latest week. Source: join `cons__fct_oee` → `cons__dim_equipment` (throughput) → `cons__dim_product` (revenue_per_unit). Shows which line is contributing most to financial exposure.

Rendered as `st.metric` for the plant total, plus a small `st.dataframe` or `st.columns` card row for per-line detail.

**Section C: Historical OEE trend** (line chart)

Carried forward from current `1_Overview.py`'s OEE trend section. Line chart of `availability_pct` or `oee_pct` by `line_name` over `period_week`. Keeps the metric selector radio (`Availability %` / `OEE %`) and monthly rollup segmented control.

**Section D: Predicted OEE / Forecast availability**

Carried forward from current `3_Forecast_OEE.py`. 8-week projected availability chart with risk-window shading, demand-peak overlap markers, and the honesty-caveat captions. No structural change to the chart logic, just relocated to this page.

**Section E: Asset risk summary**

Health status cards per machine (from current Overview's "Machine health" section). Each card: machine name, line, health badge (Healthy/Watch/At-Risk), predicted RUL hours. Compact card row, not full sensor detail (that's on Diagnostics).

### 4.3 Production (page 2 — Production Planner home)

This page gives the production planner visibility into demand, capacity, and where machine health threatens delivery.

**Section A: Order trend** (line chart)

Historical order volume from `cons__fct_order`, aggregated by `order_week` and `line_name` (joined through `cons__dim_equipment` product→line mapping). Shows trailing order volume trend with a 4-week rolling average overlay (already computed in the priority score model's `trailing_4wk_avg_order_units`).

**Section B: Future orders / demand look-ahead**

Forward-looking order weeks from `cons__fct_order` where `order_week > CURRENT_DATE`. Rendered as a table or bar chart showing upcoming weekly demand by line. If no future order data exists (depends on data generator's horizon), show the latest 4 weeks as "recent demand" with a caption explaining no forward orders are loaded.

**Section C: Production batch gap** (`st.container(border=True)`)

The core planner question: "Can my machines keep up with demand given their predicted health?"

Per equipment, compare:
- **Required run-hours next 4 weeks**: `required_run_hours_next_4wk` from `cons__fct_priority_score`
- **Predicted remaining life**: `predicted_rul_hours` from `cons__fct_priority_score`
- **Gap**: `predicted_rul_hours − required_run_hours_next_4wk` (positive = surplus, negative = shortfall)

Rendered as a horizontal bar chart or table with color coding:
- Green: surplus > 50% of required
- Yellow: surplus < 50% of required
- Red: shortfall (predicted failure before demand is met)

This is the "Survives its own demand?" concept from the current Prioritization page, reframed as a production-planning view with the gap quantified, not just a yes/no badge.

**Section D: Machine health impact on delivery**

Compact health cards (same as Plant Dashboard §E) filtered or sorted by which machines are on lines with the highest demand pressure. Shows the planner which machines to watch — a bridge to the Diagnostics page for deeper investigation.

### 4.4 Diagnostics (page 3 — Maintenance Supervisor home)

This page consolidates the current Overview's sensor detail and the Prioritization page's priority score table.

**Section A: Line selector** (`st.radio` or `st.selectbox`, horizontal)

Options: `All Lines`, `Caliper`, `Engine Head`. Filters sections B and C to the selected line's equipment. Default: `All Lines`.

**Section B: Sensor diagnostics** (per machine, expandable)

Carried forward from current `1_Overview.py`'s sensor detail section. Per sensor-enabled machine on the selected line: 3 mini charts (vibration, temperature, RPM) showing the last N readings. Uses the existing `split_trend_and_latest_tick` logic for handling data-gen gaps.

Key change from current app: the line selector (§A) pre-filters which machines are shown, so a supervisor focused on a single line doesn't have to scroll past irrelevant machines.

**Section C: Priority risk score**

Carried forward from current `2_Prioritization.py`. Ranked table of `cons__fct_priority_score` + stacked bar chart of weighted contributing factors + "Survives its own demand?" badge. Filtered by selected line (§A).

Row click → expands the clicked machine's sensor detail in §B (same `st.session_state["jump_to_equipment"]` cross-reference pattern, but now intra-page instead of cross-page).

### 4.5 Chat (page 4 — common)

No structural change. Current `4_Chat.py` is already persona-aware (routes to the correct agent, shows agent capabilities, per-persona chat history). Only cosmetic change: page number changes from `4` to `4` (stays same) and the sidebar persona indicator is now visible.

---

## 5. New Synthetic Financial Inputs

### 5.1 `revenue_per_unit` on `cons__dim_product`

**What**: A new column representing the revenue (or contribution margin) per unit of product sold.

**Where**: Added to the data generator → `raw.product` → `std__product` → `cons__dim_product` pipeline. The generator produces realistic per-product values (e.g. calipers: $45-65/unit, engine heads: $120-180/unit — order-of-magnitude realistic for machined automotive components).

**Used by**: Plant Dashboard §B (per-line lost revenue calculation).

**Formula**: `line_lost_revenue = SUM(breakdown_hours_per_equipment × equipment.throughput_units_per_hour × product.revenue_per_unit)` for each equipment on the line, for a given `period_week`.

### 5.2 `cost_per_hour_of_downtime` dbt var

**What**: A single fleet-wide cost rate for unplanned downtime, representing the fully-loaded cost of a line being down (labor, overhead, opportunity cost) independent of product mix.

**Where**: dbt var in `dbt_project.yml`, default value (e.g. `$2,500/hour` — industry-typical for a mid-size CNC machining operation). Overridable via `--vars` or env var.

**Used by**: Plant Dashboard §B (plant-level rollup headline figure).

**Formula**: `plant_lost_revenue = SUM(all_lines_breakdown_hours) × cost_per_hour_of_downtime`.

### 5.3 Relationship between the two

The per-line calculation (§5.1) and the plant rollup (§5.2) are intentionally two different lenses on the same loss:
- **Per-line** (product-level granularity): answers "which line is losing the most revenue" — useful for prioritization
- **Plant rollup** (blended rate): answers "what's the total financial exposure" — useful for executive summary

They won't necessarily agree numerically (the blended rate is a simplification), and that's acceptable and realistic — real manufacturing finance uses both views. The mockup will show both with a caption explaining the difference.

---

## 6. Mockup File Structure

```
mockup_v2/
├── index.html              # Shell: sidebar + page routing (imports shared CSS/JS)
├── styles.css              # Shared styles (Streamlit-like visual language)
├── shared.js               # Shared state, persona logic, sidebar rendering
├── data.js                 # Mock data (machines, OEE, orders, priority, financial)
├── page_choose_persona.js  # Page 0: Choose Persona
├── page_plant_dashboard.js # Page 1: Plant Dashboard
├── page_production.js      # Page 2: Production
├── page_diagnostics.js     # Page 3: Diagnostics
└── page_chat.js            # Page 4: Chat
```

**Visual language**: Carry forward from `mockup/styles.css` — Snowflake-blue theme (`#29b5e8`/`#11567f`), card-based layout with border+shadow, health badges (green/amber/red), inline SVG charts. Updated for the new page structure and financial metrics.

**Streamlit fidelity**: Cards rendered as bordered containers (matching `st.container(border=True)`), metrics as large number + delta + caption (matching `st.metric`), sidebar as a fixed left panel with persona label, charts as placeholder SVG (same inline-chart-helper approach as old mockup). No Streamlit widget interactivity — this is a visual reference, not a functional prototype.

---

## 7. What Changes vs. Current App

| Current page | Fate | Content moves to |
|---|---|---|
| `0_Choose_Persona.py` | Kept, routing updated | — |
| `1_Overview.py` | Retired | Health cards → Plant Dashboard §E + Production §D; OEE trend → Plant Dashboard §C; Sensor detail → Diagnostics §B; Order-driven priority signals → removed (subsumed by Production §A-C) |
| `2_Prioritization.py` | Retired | Priority table + factor chart → Diagnostics §C; "Survives demand" → Diagnostics §C + Production §C |
| `3_Forecast_OEE.py` | Retired | Forecast chart → Plant Dashboard §D |
| `4_Chat.py` | Kept, cosmetic only | — |
| — (new) | `1_Plant_Dashboard.py` | New page |
| — (new) | `2_Production.py` | New page |
| — (new) | `3_Diagnostics.py` | New page |

---

## 8. Named Invariants to Preserve

These correctness constraints from the existing codebase must carry forward into the new pages:

1. **OEE stored as 0-1 ratio**: `cons__fct_oee.availability_pct` and `oee_pct` are 0-1 floats, not percentages. Multiply by 100 at display time (currently done in `load_oee_trend()`).
2. **Sensor detail reads `cons__fct_sensor_reading` directly**: The named exception to Consumption-only-via-derived-metrics sourcing (doc-flagged, `1_Overview.py` comment). Still Consumption layer, not Raw/Std/FEAST.
3. **Priority score factor weights**: `0.40 × rul_urgency + 0.25 × demand_pressure + 0.20 × inventory_buffer + 0.15 × spare_part_readiness`, hardcoded in `cons__fct_priority_score.sql`. The stacked bar chart must use these same weights.
4. **Predicted failure week is directional, not calibrated**: The honesty-caveat captions from `3_Forecast_OEE.py` (concordance index 0.94 but median absolute error ~182h on only 5 test rows) must appear wherever predicted RUL is translated to a calendar date.
5. **Chat persona isolation**: Per-persona chat histories (`st.session_state.chat_histories[persona]`), not a shared list. Agent name resolved from `PERSONAS[persona]["agent_name"]`.
6. **Data-gen gap handling**: `split_trend_and_latest_tick()` logic for sensor charts — the bulk historical load and isolated demo ticks are separated by months, must not be plotted on one continuous axis.

---

## 9. Out of Scope / Deferred

- **Actually building the new pages**: This design doc freezes the structure. Implementation is a follow-up `Developer-agent` dispatch.
- **Building the mockup content**: The mockup file creation in `mockup_v2/` is also a follow-up dispatch.
- **New dbt models for financial metrics**: Adding `revenue_per_unit` to the generator/pipeline and `cost_per_hour_of_downtime` to `dbt_project.yml` are implementation tasks, not design decisions.
- **Impact Statement page**: The old mockup had one (4 metric cards — downtime reduction %, time-to-detect improvement, at-risk revenue protected, concordance index). Not included in the 5-page structure since the user's brief didn't mention it and those metrics are better shown inline (financial risk in Plant Dashboard, concordance index as a caption on forecast charts). Can be revisited if the user wants it back.
- **Machine filter dropdown**: The old mockup had a sidebar machine filter. Not included — the line selector on Diagnostics (§4.4 §A) and the page-level content scoping per persona serve the same purpose more naturally.

---

## 10. Open Items for Developer-agent

1. Confirm `cons__dim_product` exists as a standalone model or if product attributes live only on `cons__dim_equipment` — the `revenue_per_unit` column placement depends on this. Current schema: `cons__dim_equipment` has `product_id` + `variant` but no product dimension table in Consumption; `std__product` exists in Standardized. May need a new `cons__dim_product` passthrough.
2. Choose realistic synthetic `revenue_per_unit` values and `cost_per_hour_of_downtime` default — suggested ranges in §5.1/§5.2, but the generator author should pick final numbers.
3. The production batch gap chart (§4.3 §C) needs design-time decision on chart type (horizontal bar vs. diverging bar vs. bullet chart) — deferred to implementation since it depends on what looks cleanest in Plotly within Streamlit's layout constraints.
