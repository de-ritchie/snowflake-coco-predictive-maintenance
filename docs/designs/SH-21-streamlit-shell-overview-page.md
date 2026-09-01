# Design: SH-21 — S-APP-1: Streamlit shell + Overview page

Status: **Frozen** — brainstormed interactively with the user, frozen on explicit signal ("Let us freeze this for a first version and go ahead with design doc"). Reconciled 2026-09-01 post-implementation/review — see decisions #6, #10, #13 and §5 for what changed from the original freeze and why.
Branch: `feature/SH-2-21-streamlit-shell-overview-page` (already checked out)
Epic: SH-2 | Story: SH-21 (status: In Progress)
Traces to: FR-CC-01, `docs/04-*-LLD.md` Module 8 §0/§1 (partial)

Jira description: "T1: App shell, sidebar (no persona switcher yet - single agent). T2: Health status cards from `cons.fct_anomaly_result`."

---

## 1. Problem & scope

Build the first version of the Streamlit app shell plus its Overview page:

| Task | Scope |
|---|---|
| T1 | App shell + sidebar. No persona switcher yet — single agent for now. |
| T2 | Health status cards sourced from `cons.fct_anomaly_result`. |

No OEE trend chart, no sidebar machine filter, no persona switcher in this story — see §3 for what's explicitly cut vs. deferred.

---

## 2. Ground truth validated against Snowflake

- `cons.dim_equipment` has **1 row today** (`CNC_BORING`, Caliper line, sensor-enabled). Build for N machines, render whatever exists — not a hardcoded 3-slot grid.
- `cons.fct_anomaly_result` columns: `equipment_id`, `reading_ts`, `is_anomaly BOOLEAN`, `anomaly_score` (signed, real range roughly -0.2 to +0.15 — **not** a 0–1 scale).
- `cons.fct_sensor_reading`: 3 sensor types (`VIBRATION`, `TEMPERATURE`, `RPM`), ~4-hour real cadence.
- `cons.fct_oee`, `cons.fct_priority_score`, `cons.fct_rul_prediction`, `cons.fct_order` **do not exist yet** (later Epics) — no OEE trend chart possible in this story.
- No semantic view, no Cortex Agent, no persona switcher exist yet in this project.

---

## 3. Agreed decisions

| # | Decision | Resolution |
|---|---|---|
| 1 | **Health badge logic** | **at-risk** if the latest `fct_anomaly_result` row for a machine has `is_anomaly = TRUE`; **watch** if the latest row is not anomalous but at least one `is_anomaly = TRUE` occurred in the trailing 48 hours (~12 readings at ~4h cadence); else **healthy**. |
| 2 | **Sidebar machine filter** | Cut entirely from this story — a no-op with only 1 machine today. Whether it belongs globally at all is deferred, not decided. |
| 3 | **OEE trend chart** | Cut entirely from this story — no backing table exists yet. |
| 4 | **Sidebar contents for SH-21** | "Inject next tick" button only. No persona switcher, no machine filter. |
| 5 | **Demo-only control visual convention** | Whether to lock a visual convention now for future demo-only controls (e.g. a persona switcher) is explicitly **deferred**, not decided in this story. |
| 6 | **Sensor detail default lookback** | ~~Last 7 calendar days.~~ **Updated 2026-09-01 per Reviewer-agent finding**: real data has a dense historical block ending 2026-01-23 and a single isolated demo-tick reading at 2026-08-31 (~7-month gap) — a calendar window anchored on the latest reading only ever captured that one lone point, rendering as a flat/empty-looking chart. Implemented instead: query the last `READINGS_PER_SENSOR` (50) readings per sensor type, via one query per machine with a combined `LIMIT` of `READINGS_PER_SENSOR * len(SENSOR_TYPES)` (works out to 50/type only because timestamps are currently 1:1 aligned across the 3 sensor types — see the assumption flagged in §5). A `split_trend_and_latest_tick()` helper (gap threshold: 2 days) then separates any trailing isolated reading(s) from the dense trend so the chart's x-axis isn't stretched flat by the gap; the isolated tick is surfaced as a caption under the chart instead of being plotted on the line. |
| 7 | **Health cards implementation** | `st.metric` + custom color badge markdown, laid out via `st.columns(N)` driven by however many rows `dim_equipment` returns. |
| 8 | **Sensor detail implementation** | `st.expander` per machine, one Plotly line chart per sensor type (own y-scale each), last `READINGS_PER_SENSOR` (50) readings per type (see updated decision #6), sourced **directly** from `cons.fct_sensor_reading`. |
| 9 | **File structure** | `oee_command_center_app/streamlit_app.py` (shell + sidebar) + `oee_command_center_app/pages/1_Overview.py`, using Streamlit's native multipage `pages/` convention. |
| 10 | **Connection** | ~~`st.connection("snowflake", connection_name="snow-coco")`~~ **Corrected 2026-09-01**: `st.connection("snow-coco", type="snowflake")` — the original example is factually wrong and raises `TypeError: got multiple values for keyword argument 'connection_name'` against streamlit==1.62, since Streamlit treats the first positional arg as the connection name itself, not the connection type. Verified live against the real `snow-coco` connection. Still this project's standard connection convention, just with the call written correctly. |
| 11 | **Sidebar tick button visibility** | Gated behind the `?demo=1` query param, hidden by default. |
| 12 | **`streamlit` dependency** | Not yet in `pyproject.toml` (only `plotly`/`pandas` etc. are today) — add via `uv add streamlit`, don't hand-edit `pyproject.toml`/`uv.lock`. Deferred to `Developer-agent`. |
| 13 | **Health card styling** | Each machine's `st.metric` + badge wrapped in `st.container(border=True)`. Added live during manual review (2026-09-01) — not in the original freeze; the cards didn't visually read as cards without a border. Presentational only, no data/query implications. |

---

## 4. Affected files & tables

**Reads:**
- `cons.dim_equipment`
- `cons.fct_anomaly_result`
- `cons.fct_sensor_reading`

**New files:**
- `oee_command_center_app/streamlit_app.py`
- `oee_command_center_app/pages/1_Overview.py`

---

## 5. Named invariants to preserve

- **Consumption-only sourcing (FR-PL-05)**: this project's standing rule is Consumption-only — never query Raw/Standardized/FEAST directly. **This story's sensor-detail chart (decision #8) is an explicit, named exception**: it reads `cons.fct_sensor_reading` directly (still Consumption layer, not Raw/Std/FEAST, so it does not actually violate FR-PL-05's letter) but is called out here explicitly so `Reviewer-agent` checks that nothing else on this page reaches below the Consumption layer.
- **`snow-coco` connection convention**: all Snowflake access from this app must go through `st.connection("snow-coco", type="snowflake")` (decision #10, corrected 2026-09-01) — no hardcoded credentials, no alternate connection name.

**Known assumption / risk (flagged 2026-09-01 during doc reconciliation, not an invariant to preserve but a fragility to watch)**: `load_sensor_history()`'s combined `LIMIT` (`READINGS_PER_SENSOR * len(SENSOR_TYPES)`, i.e. 150 rows total) only yields ~50 readings per individual sensor type because the current dataset has all 3 sensor types sharing the exact same reading timestamps 1:1. This is an artifact of the current thin-data generator, not a guaranteed property of the schema. If a future data-gen revision adds sensors with independent cadences (e.g. VIBRATION sampled more frequently than RPM), this combined-limit approach would silently skew per-type coverage and needs revisiting — e.g. move to a per-sensor-type `LIMIT`/window (such as a `ROW_NUMBER() OVER (PARTITION BY sensor_type ORDER BY reading_ts DESC)` filter) instead of one flat `LIMIT` across all types.

---

## 6. Explicitly deferred / open items

- Item #12 above (`uv add streamlit`) — for `Developer-agent` to execute as part of implementation, not a design decision to pre-empt by hand-editing `pyproject.toml`/`uv.lock`.
- Sidebar machine filter (decision #2) — deferred to a future story once more than 1 machine exists; not part of SH-21.
- Demo-only control visual convention (decision #5) — deferred; no convention locked in this story even though the "Inject next tick" button is itself a demo-only control.
- OEE trend chart (decision #3) — deferred until `cons.fct_oee` exists (later Epic).
