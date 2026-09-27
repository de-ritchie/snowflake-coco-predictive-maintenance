# Design: SH-48 — Forecast OEE page + Priority Queue page (S-RUL-7)

Status: **Design frozen** (brainstorm confirmed by user; this doc is the frozen synthesis) → `Developer-agent` build → **`Reviewer-agent` clean PASS (2026-09-27), all claims independently re-verified live** (see §10 for results). One real deviation found and fixed during build: `cons.fct_oee.availability_pct` is stored as a 0-1 ratio at the source, not 0-100 as §4.5's dip formula originally assumed — this produced negative "projected availability" values before the fix; see §4.5's note for the fix and where it lives. One doc-only typo also fixed (§4.6, double-brace → single-brace f-string placeholder — the shipped code was already correct, only this doc needed updating). `Documenter-agent` reconciliation complete. **Scope extension frozen (2026-09-27, same session)**: two Overview-page visual enhancements bundled into this same still-open branch/doc — see §11. **§11 itself: `Reviewer-agent` clean PASS (2026-09-27)** — zero code bugs, every data claim independently re-verified live on a different month/query than Developer-agent's own checks; two real implementation-detail deviations from §11's literal code sketches found and confirmed genuinely necessary (invalid `make_subplots` `specs` shape for the installed Plotly version, `PERIOD_WEEK` dtype conversion) — see §11.1 and §11.6.
Branch: `feature/SH-4-48-forecast-oee-priority-queue-pages`
Epic: EPIC-RUL | Story: SH-48 (S-RUL-7: "Forecast OEE page + Priority Queue page")
Traces to: [docs/04-8-LLD.md](../04-8-LLD.md) §2 (Priority Queue, FR-CC-02), §3 (Forecast OEE, FR-CC-03), §1 (Overview, FR-CC-01 — the OEE trend chart's own spec, closed by §11.1 below), [docs/04-6-LLD.md](../04-6-LLD.md) (`PriorityScore` semantic entity, already specified as a target entity — deferred by SH-27, built here), [docs/designs/SH-47-priority-score.md](SH-47-priority-score.md) (`cons.fct_priority_score`'s exact columns/grain — this story's primary data source), [docs/designs/SH-46-rul-model-evaluation.md](SH-46-rul-model-evaluation.md) (concordance index / outlier / small-sample findings this story's honesty caveats cite), [docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md](SH-27-28-30-31-32-33-semantic-view-agent-chat.md) (semantic view / agent DDL conventions), `scripts/07_post_setup.sql`, `oee_command_center_app/pages/1_Overview.py` (page-code convention this story follows).

**Follows**: SH-47 (merged, `cons.fct_priority_score` — `equipment_id, score_ts, predicted_rul_hours, is_anomaly, anomaly_score, rul_urgency, demand_pressure, inventory_buffer, spare_part_readiness, priority_score, required_run_hours_next_4wk`, latest-per-equipment grain), SH-46 (merged, RUL model evaluation results this story's caveats quote directly), SH-69 (merged, `cons.fct_order`/`fct_oee`/`fct_maintenance_event`), SH-42 (merged, `1_Overview.py`'s existing "Order-driven priority signals" section and its `load_priority_signals()` query — the section being visually upgraded by §11.2 below).

---

## 1. Scope

Build the two remaining P1 Streamlit pages named in `docs/04-8-LLD.md` §2/§3:

1. **Priority Queue** (FR-CC-02): ranked table of `cons.fct_priority_score` + a stacked bar chart showing each machine's 4 weighted contributing factors + a "survives its own demand?" badge per machine.
2. **Forecast OEE** (FR-CC-03): an 8-week-lookahead projected-Availability chart that translates `cons.fct_priority_score.predicted_rul_hours` into a calendar failure week, shades it against `cons.fct_order`'s demand peaks, and is explicit — via `st.caption`, not buried in a tooltip — about exactly how uncertain that translation is, citing SH-46's own evaluation numbers by name.

Also **in scope**: adding the `PriorityScore` semantic-view entity (already specified as a target entity in `docs/04-6-LLD.md`, deferred by SH-27 pending `cons.fct_priority_score`'s existence — that blocker is now resolved by SH-47) to `scripts/07_post_setup.sql`, 2 new `AI_VERIFIED_QUERIES`, and an update to `maintenance_supervisor_agent`'s instructions so it stops treating priority/RUL questions as out of scope.

**Not in scope**: `explain_prediction` (SHAP-based, feature-level "why" tool) — still not enabled, and this story's agent-instruction update is explicit about *not* conflating "report a predicted value" with "explain a predicted value" (§4). Ticketing. Persona switching. Any change to `cons.fct_priority_score`'s own SQL (SH-47's job, already shipped) — this story only *reads* it.

**Scope extension (§11, bundled 2026-09-27)**: two Overview-page (`1_Overview.py`) visual enhancements discovered/requested live during manual testing of this story's own build — an OEE trend chart (closing a pre-existing `docs/04-8-LLD.md` §1 gap, unrelated in data source to Priority Queue/Forecast OEE but bundled here because it shares the same file and the same testing session) and a visual upgrade to the existing "Order-driven priority signals" section (SH-42's work, replacing text-only latest-snapshot rendering with a historical trend chart + metric cards). See §11 for full rationale and detail.

---

## 2. Page numbering

Existing pages: `1_Overview.py`, `2_Chat.py` (Chat was built before Priority Queue/Forecast OEE in SH-27's slice, ahead of `docs/04-8-LLD.md`'s own §-ordering — Streamlit's numeric prefix only controls sidebar order, it doesn't need to mirror the LLD's section numbers). New pages take the next two slots, in the LLD's own relative order (Priority Queue §2 before Forecast OEE §3):

- `oee_command_center_app/pages/3_Priority_Queue.py`
- `oee_command_center_app/pages/4_Forecast_OEE.py`

Both follow `1_Overview.py`'s established page convention exactly: `from streamlit_app import get_connection, render_sidebar`, `st.set_page_config(page_title=..., layout="wide")` at top, `render_sidebar()` then content, `@st.cache_data(ttl=300)` on every query function, Plotly (not native `st.line_chart`/`st.bar_chart`) for anything beyond a trivial single-series line — matching `1_Overview.py`'s own `px.line` usage and Module 8's "Plotly likely needed for the stacked-bar look" deferred note.

---

## 3. Priority Queue page (`3_Priority_Queue.py`)

### 3.1 Data source

Single query against `cons.cons__fct_priority_score` (already latest-per-equipment, SH-47 §4 — no `QUALIFY`/re-derivation needed here) joined to `cons.cons__dim_equipment` for display columns:

```sql
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
```

### 3.2 Ranked table

`st.dataframe`, one row per sensor-enabled machine, sorted `priority_score` descending (already sorted by the query). Columns: `equipment_name`, `line_name`, `priority_score` (rounded, e.g. `.1f`), the 4 raw `[0,1]` factor columns (`rul_urgency`, `demand_pressure`, `inventory_buffer`, `spare_part_readiness` — shown as-is so "why this ranking" is inspectable per the LLD's explicit requirement, not re-normalized or relabeled), and the "survives its own demand?" badge column (§3.4).

### 3.3 Stacked bar chart

Plotly (`px.bar` with `barmode="stack"`), one bar per machine (x-axis = `equipment_name`), 4 stacked segments per bar = each factor's **weighted contribution in points out of 100**, computed client-side from the same row (not re-derived server-side, no new SQL):

```python
df["rul_urgency_pts"] = 40 * df["RUL_URGENCY"]
df["demand_pressure_pts"] = 25 * df["DEMAND_PRESSURE"]
df["inventory_buffer_pts"] = 20 * df["INVENTORY_BUFFER"]
df["spare_part_readiness_pts"] = 15 * df["SPARE_PART_READINESS"]
```

These 4 columns melt into Plotly's long-form input (`pd.melt` on `equipment_name` + the 4 `_pts` columns), stacked bar height = `priority_score` exactly (the 4 weighted terms sum to the composite by construction, per SH-47 §2's formula) — this is the visual proof that the bar's total height *is* the ranked table's `priority_score` column, not an independently-computed number that could silently drift from it. Legend labels the 4 segments by factor name, not `_pts` suffix.

### 3.4 "Survives its own demand?" badge

Compares `predicted_rul_hours` (hours of life the machine has left, right now) against `required_run_hours_next_4wk` (hours the machine needs to actually run over the next 4 weeks to meet firm demand for its product/variant) — **both columns read verbatim from `cons.fct_priority_score`, neither re-derived**. This is the SH-47-invariant-echoing constraint from this story's own brief: `required_run_hours_next_4wk` must match SH-47's column exactly, not be recomputed with a different formula here.

```python
def survives_badge(predicted_rul_hours: float, required_run_hours_next_4wk: float) -> str:
    if predicted_rul_hours >= required_run_hours_next_4wk:
        return render_badge_html("Yes", fg="#2e7d32", bg="#e8f5e9")
    return render_badge_html("No", fg="#c62828", bg="#ffebee")
```

`"No"` reads as: *this machine is predicted to fail before it can finish running the volume already on order for the next 4 weeks* — a materially different, sharper claim than the priority score alone (which already factors in `demand_pressure`/`rul_urgency` separately, but never states the two against each other as a single yes/no survivability check). Badge styling reuses `1_Overview.py`'s `BADGE_STYLE`/`render_badge` pattern (2-color pill, `unsafe_allow_html=True`) — extend that dict with a 2-entry style (`survives`/`at-risk-of-demand`) rather than inventing a new badge component.

### 3.5 Row-click cross-navigation (LLD §2's stated requirement, lightweight)

LLD §2 asks for row click → jump to Overview's sensor-detail expander for that machine. Implementation: a small "View sensor detail" button per row (`st.dataframe` doesn't support native row-click callbacks without `st.data_editor`/`on_select`, which is a build-time API check) that sets `st.session_state["jump_to_equipment"] = equipment_id` and `st.switch_page("pages/1_Overview.py")`. `1_Overview.py` gains a small addition: if `st.session_state.get("jump_to_equipment")` is set, auto-expand that machine's `st.expander` on load and clear the session-state key. Exact Streamlit API for row-selection (`st.dataframe(..., on_select=...)` vs. a plain button column) is a build-time detail — `Developer-agent` picks whichever the installed Streamlit version actually supports; the session-state hand-off mechanism above is the frozen part.

**Implementation note (not a deviation)**: as shipped, `1_Overview.py` reads the hand-off key via `st.session_state.pop("jump_to_equipment", None)` rather than this doc's suggested get-then-manual-clear pattern. `pop` retrieves and clears the key in one call, so it self-clears atomically instead of needing a separate `del`/reset line — a minor implementation improvement `Reviewer-agent` confirmed as correct and equivalent to the frozen spec's intent, not something requiring justification.

---

## 4. Forecast OEE page (`4_Forecast_OEE.py`)

### 4.1 Assumed-downtime scalar — data-driven, not hardcoded

BRD assumption 8 originally hardcoded an assumed downtime figure per failure event. This story replaces that with a real, data-driven scalar:

```sql
SELECT MEDIAN(duration_hours) AS assumed_downtime_hours
FROM cons.cons__fct_maintenance_event
WHERE event_type = 'BREAKDOWN'
```

`MEDIAN`, not `AVG` — robust to any one unusually long breakdown, same robustness rationale SH-46 §5 already established for `median_ae` over `mae`/`rmse` on a small sample. This single scalar is queried once per page load (`@st.cache_data(ttl=300)`) and reused for every machine/week's projected dip (§4.4) — not per-machine, since `cons.fct_maintenance_event` doesn't have enough breakdown rows per machine yet to support a per-machine median without an even smaller-sample caveat than the fleet-wide one already needed.

### 4.2 Failure-week derivation

Reads `cons.fct_priority_score` directly (already latest-per-equipment; SH-47's own persisted `predicted_rul_hours`/`score_ts` — no re-derivation of "latest RUL per machine" from `cons.fct_rul_prediction`'s full per-tick history, which would duplicate work `cons.fct_priority_score` already did):

```sql
SELECT
    equipment_id,
    score_ts,
    predicted_rul_hours,
    DATE_TRUNC('week', DATEADD('hour', predicted_rul_hours, score_ts)) AS predicted_failure_week
FROM cons.cons__fct_priority_score
```

`predicted_failure_week` is a calendar-week bucket derived by walking `predicted_rul_hours` forward in wall-clock time from `score_ts` — this is BRD assumption 8's "translate RUL hours to calendar-day risk windows," made explicit and queryable rather than a hidden mental conversion.

### 4.3 8-week lookahead spine + demand-peak join

Week spine anchored on `cons.fct_priority_score`'s own latest `score_ts` (not `cons.fct_oee`'s latest `period_week`, which lags behind live sensor data and would anchor the lookahead in the past relative to what the RUL model is actually scoring):

```sql
WITH week_spine AS (
    SELECT DATEADD('week', SEQ4(), DATE_TRUNC('week', (SELECT MAX(score_ts) FROM cons.cons__fct_priority_score))) AS forecast_week
    FROM TABLE(GENERATOR(ROWCOUNT => 8))
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
    SELECT line_name, order_week, line_order_units,
           line_order_units = MAX(line_order_units) OVER (PARTITION BY line_name) AS is_demand_peak_week
    FROM demand_by_line_week
)
```

`is_demand_peak_week`: the single highest-order week per line across all of `cons.fct_order`'s history (not scoped to the 8-week window alone) — a demand "peak" is meaningful relative to the line's own typical volume, not just the highest of 8 arbitrary forward weeks, most of which may be flat extrapolations of the same order pattern.

### 4.4 Runtime risk-window selector

`st.slider("Risk window (± days around predicted failure)", min_value=1, max_value=14, value=7)` in the page body (not the sidebar — this control is specific to this page's chart, not a global app setting like the persona switcher). The selected `risk_window_days` widens/narrows which `forecast_week` rows count as "at risk" for a given machine's `predicted_failure_week`:

```python
at_risk_week = df["FORECAST_WEEK"].between(
    df["PREDICTED_FAILURE_WEEK"] - pd.Timedelta(days=risk_window_days),
    df["PREDICTED_FAILURE_WEEK"] + pd.Timedelta(days=risk_window_days),
)
```

A week flagged both `at_risk_week` and `is_demand_peak_week` (joined on that machine's `line_name`) is the "flagged machine's predicted failure window overlaps a demand peak" moment LLD §3 names as this chart's whole reason for existing (BRD §7 demo beat 7).

### 4.5 Projected Availability chart logic

One Plotly line per `line_name`, x = `forecast_week` (8 points), y = projected `availability_pct`:

```python
baseline_availability_pct = latest_actual_oee_df.set_index("LINE_NAME")["AVAILABILITY_PCT"]  # cons.fct_oee, latest period_week per line
scheduled_hours = latest_actual_oee_df.set_index("LINE_NAME")["SCHEDULED_HOURS"]

projected_pct = baseline_availability_pct[line]
if week_is_at_risk_and_demand_peak:
    projected_pct -= 100 * assumed_downtime_hours / scheduled_hours[line]
```

Baseline = that line's own latest actual `availability_pct`/`scheduled_hours` from `cons.fct_oee` (flat carry-forward for weeks with no flagged risk — this chart is not attempting to forecast organic availability drift, only the *maintenance-driven* dip). The dip is applied only to weeks that are simultaneously `at_risk_week` (§4.4) for that line's flagged machine *and* `is_demand_peak_week` (§4.3) — a week that's merely `at_risk` but demand is low doesn't get the same visual emphasis (shaded/annotated differently, e.g. a lighter dip or a marker without the red demand-peak-overlap annotation), matching LLD §3's framing that the overlap itself, not either condition alone, is the point.

**Deviation found during build — `availability_pct` scale (reconciled 2026-09-27)**: this formula was drafted assuming `cons.fct_oee.availability_pct` is a 0-100 percentage. It is not — at the source it's stored as a 0-1 ratio. Using it as-drafted produced negative "projected availability" values (subtracting a 0-100-scale dip from a 0-1-scale baseline). `Developer-agent`'s fix: convert once, at load time, in `load_latest_oee()` (`oee_command_center_app/pages/4_Forecast_OEE.py`) — multiply `availability_pct` by 100 immediately on load, before it reaches `baseline_availability_pct` or the dip calculation above. `Reviewer-agent` confirmed this is the single consistent conversion point and that every downstream formula (baseline carry-forward, dip subtraction) correctly consumes the already-corrected 0-100 value — no double-conversion, no remaining 0-1-scale reference elsewhere in the page. Future readers: `cons.fct_oee.availability_pct` is a 0-1 ratio at the source; treat any 0-100-scale usage of it in this page as post-load-converted, not native.

### 4.6 Honesty caveats — exact `st.caption` text, citing SH-46 by finding, not generically

Two distinct captions, directly under the chart, each naming a specific SH-46 §14 result rather than a generic "this is uncertain" hedge:

```python
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
```

These three captions are a required part of the page, not optional polish — Reviewer-agent invariant 1 (§6) checks for their presence.

---

## 5. Semantic view update (`scripts/07_post_setup.sql`)

### 5.1 `PriorityScore` entity — new `TABLES (...)` entry

`docs/04-6-LLD.md` already lists `PriorityScore` / `cons.fct_priority_score` / `equipment_id, score_ts` as a target entity (deferred by SH-27 pending the table's existence, which SH-47 has since resolved) — no LLD change needed, this is that deferred entity finally getting built:

```sql
priority_score AS snowcomotive.cons.cons__fct_priority_score
  PRIMARY KEY (equipment_id, score_ts)
  WITH SYNONYMS ('priority', 'priority score', 'RUL', 'remaining useful life', 'urgency ranking')
  COMMENT = 'Composite 0-100 priority score per machine (RUL urgency, demand pressure, inventory buffer, spare-part readiness) -- always latest-per-equipment, per docs/designs/SH-47-priority-score.md'
```

### 5.2 Relationship — new `RELATIONSHIPS (...)` entry

```sql
priority_score_to_machine AS priority_score (equipment_id) REFERENCES machine (equipment_id)
```

### 5.3 Facts — new `FACTS (...)` entries

```sql
priority_score.priority_score AS priority_score,
priority_score.predicted_rul_hours AS predicted_rul_hours,
priority_score.rul_urgency AS rul_urgency,
priority_score.demand_pressure AS demand_pressure,
priority_score.inventory_buffer AS inventory_buffer,
priority_score.spare_part_readiness AS spare_part_readiness,
priority_score.required_run_hours_next_4wk AS required_run_hours_next_4wk
```

### 5.4 Dimensions — new `DIMENSIONS (...)` entry

```sql
priority_score.score_ts AS score_ts
```

(`equipment_id` is already a `machine` dimension via the relationship — no duplicate dimension needed on `priority_score` itself.)

### 5.5 Two new `AI_VERIFIED_QUERIES`

**Query 1 — "Which machines have the highest priority score right now, and why?"**

```sql
priority_score_ranking AS (
  QUESTION 'Which machines have the highest priority score right now, and why?'
  SQL 'SELECT
    eq.equipment_id,
    eq.equipment_name,
    eq.line_name,
    ps.priority_score,
    ps.rul_urgency,
    ps.demand_pressure,
    ps.inventory_buffer,
    ps.spare_part_readiness
FROM snowcomotive.cons.cons__fct_priority_score ps
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = ps.equipment_id
ORDER BY ps.priority_score DESC'
)
```

**Query 2 — "Which machines are at risk of failing before they can meet the next 4 weeks of demand?"**

```sql
priority_score_demand_survivability AS (
  QUESTION 'Which machines are at risk of failing before they can meet the next 4 weeks of demand?'
  SQL 'SELECT
    eq.equipment_id,
    eq.equipment_name,
    eq.line_name,
    ps.predicted_rul_hours,
    ps.required_run_hours_next_4wk,
    IFF(ps.predicted_rul_hours < ps.required_run_hours_next_4wk, ''NO'', ''YES'') AS survives_next_4wk_demand
FROM snowcomotive.cons.cons__fct_priority_score ps
JOIN snowcomotive.cons.cons__dim_equipment eq
    ON eq.equipment_id = ps.equipment_id
ORDER BY ps.predicted_rul_hours - ps.required_run_hours_next_4wk ASC'
)
```

Both appended into the existing `AI_VERIFIED_QUERIES (...)` clause, after `order_driven_priority_signals` (SH-27's existing entry), same `CREATE OR REPLACE SEMANTIC VIEW` statement — not a separate DDL block.

### 5.6 Agent instruction update — exact before/after

**`response` instruction — before** (current, live in `scripts/07_post_setup.sql`):

```
  response: >
    You are the SnowComotive Maintenance Agent for a predictive-maintenance
    and OEE command center. Answer questions about machine health,
    anomalies, maintenance events, OEE, orders, and inventory, grounded
    strictly in the Analyst tool's query results against the semantic view.
    Never fabricate a health, anomaly, OEE, or inventory value. If data for
    a requested machine or time period is missing or stale, say so
    explicitly rather than guessing.
```

**`response` instruction — after**:

```
  response: >
    You are the SnowComotive Maintenance Agent for a predictive-maintenance
    and OEE command center. Answer questions about machine health,
    anomalies, maintenance events, OEE, orders, inventory, priority score,
    and predicted remaining-useful-life (RUL), grounded strictly in the
    Analyst tool's query results against the semantic view. Never fabricate
    a health, anomaly, OEE, inventory, priority-score, or RUL value. If
    data for a requested machine or time period is missing or stale, say
    so explicitly rather than guessing.
```

**`orchestration` instruction — before** (current, live in `scripts/07_post_setup.sql`):

```
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, or inventory.
    This is currently the only tool available -- do not claim to be able to
    create tickets or explain model predictions; if asked, say those
    capabilities are not enabled yet.
```

**`orchestration` instruction — after**:

```
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, inventory,
    priority score, or predicted remaining-useful-life (RUL). This is
    currently the only tool available -- do not claim to be able to create
    tickets; if asked, say ticketing is not enabled yet. Reporting a
    predicted RUL or priority-score value returned by the Analyst tool is
    expected and correct -- do not confuse this with "explaining" a
    prediction (a feature-level breakdown of why the model produced that
    specific number), which is a separate capability that is not enabled
    yet; if asked to explain why a specific prediction was made, say so
    explicitly rather than guessing at a feature-level rationale.
```

**Why this split matters**: before this change, the agent's only instruction about predictions was a blanket "do not claim to explain model predictions," worded before `PriorityScore`/RUL data was even queryable — a literal read of that sentence could make the agent refuse to report a predicted RUL value at all, not just refuse to explain *why* the model produced it. The after-wording narrows the "not enabled" caveat to what's actually not enabled (SHAP-style feature explanation, ticketing) while explicitly green-lighting the new, now-grounded capability (reporting RUL/priority-score values via the Analyst tool).

---

## 6. Invariants for Reviewer-agent

1. Forecast OEE page must not claim a literal, calibrated failure date/number anywhere without the §4.6 caveat captions present on the same page render — check specifically for the concordance-index/median-AE citation and the outlier citation, not just a generic disclaimer.
2. Priority Queue's "survives its own demand?" badge must compare `cons.fct_priority_score.predicted_rul_hours` against `cons.fct_priority_score.required_run_hours_next_4wk` **read verbatim** — no independent re-derivation of either quantity from `cons.fct_rul_prediction`/`cons.fct_order` inside the Streamlit page itself (that would risk silently diverging from SH-47's own formula/columns).
3. Forecast OEE's failure-week derivation reads `cons.fct_priority_score` (already latest-per-equipment), not a fresh `QUALIFY ROW_NUMBER()` re-derivation against `cons.fct_rul_prediction`'s full per-tick history — avoids duplicating work SH-47 already did and avoids a second place where "latest RUL per machine" logic could drift from SH-47's own.
4. The assumed-downtime scalar (§4.1) is computed live via `MEDIAN(duration_hours)` over `cons.fct_maintenance_event WHERE event_type = 'BREAKDOWN'` — not hardcoded, and not silently swapped for `AVG` (robustness-to-outlier rationale, §4.1).
5. Semantic view: `PriorityScore` entity's primary key (`equipment_id, score_ts`) and relationship to `machine` must match `docs/04-6-LLD.md`'s already-specified entity row exactly — no drift between the LLD's pre-existing target spec and what actually gets built.
6. Both new `AI_VERIFIED_QUERIES` must run successfully standalone against live `cons.fct_priority_score`/`cons.dim_equipment` (mirrors SH-27 invariant 6) — the DDL wiring inside `CREATE SEMANTIC VIEW` is secondary to the SQL itself being correct.
7. The agent-instruction diff must match §5.6's exact before/after text — specifically, the updated `orchestration` instruction must still refuse ticketing and still refuse feature-level prediction explanation, while now explicitly permitting RUL/priority-score value reporting — verify none of the three clauses got dropped in translation from this doc to the live script.
8. No change to `cons.fct_priority_score`'s own SQL (`predictive_maintenance_dbt/models/consumption/cons__fct_priority_score.sql`) — this story only reads that table, per §1's explicit scope boundary.

---

## 7. File checklist

**New**:
- `oee_command_center_app/pages/3_Priority_Queue.py` (§3)
- `oee_command_center_app/pages/4_Forecast_OEE.py` (§4)

**Modified**:
- `scripts/07_post_setup.sql` — `PriorityScore` entity/relationship/facts/dimensions (§5.1–5.4), 2 new verified queries (§5.5), agent instruction text (§5.6) — all folded into the existing `CREATE OR REPLACE SEMANTIC VIEW`/`CREATE OR REPLACE AGENT` statements, per the file's own header comment convention ("revised in place, not replaced").
- `oee_command_center_app/pages/1_Overview.py` — small addition to honor `st.session_state["jump_to_equipment"]` set by the Priority Queue row-click hand-off (§3.5). **Extended by §11**: OEE trend chart (§11.1) and priority-signals chart upgrade (§11.2) are additional, unrelated-in-data-source changes to this same file, bundled into this story per §11.0.
- `scripts/README.md` — no new script number, but note row 07's scope now includes the `PriorityScore` entity (matches the file's own "revised in place" comment already there).

**Reads only, no changes**: `cons.cons__fct_priority_score`, `cons.cons__dim_equipment`, `cons.cons__fct_order`, `cons.cons__fct_oee`, `cons.cons__fct_maintenance_event`.

---

## 8. Explicitly deferred

- `RulPrediction` semantic entity (distinct from `PriorityScore`) — still not built; `cons.fct_priority_score` already carries `predicted_rul_hours` as a fact, which covers this story's and the agent's needs without it. No new story exists for `RulPrediction` itself.
- `explain_prediction` tool (SHAP-based feature-level explanation) — explicitly still not enabled; §5.6's instruction update is careful to say so rather than imply it now exists.
- Exact Streamlit row-selection API for Priority Queue → Overview cross-navigation (`st.dataframe(on_select=...)` vs. a plain per-row button) — §3.5 flags this as a build-time API-availability check, not frozen here.
- Per-machine (rather than fleet-wide) assumed-downtime scalar — deferred until `cons.fct_maintenance_event` has enough per-machine breakdown rows to support it without an even smaller sample-size caveat than the fleet-wide one.
- Any visual distinction between "at-risk-but-not-demand-peak" vs. "at-risk-and-demand-peak" weeks beyond a lighter/heavier shading treatment (§4.5) — exact chart styling is a build-time detail.
- Persona-specific instruction variants for the agent-instruction update (§5.6) — this update applies to the single skeleton `maintenance_supervisor_agent` object as-is; EPIC-PERSONAS's future 3-persona expansion revises this same object again, per SH-27 §5's already-established "living artifact" convention.

---

## 9. Open items

None blocking — every open question surfaced during the brainstorm (page numbering, risk-window default, badge naming, agent-instruction wording) was resolved to a concrete decision above. `Developer-agent` should still spot-check `cons.fct_priority_score`'s live column names against Snowsight before wiring the semantic view additions, per this project's standing "verify before relying on it" discipline (SH-27 §0, SH-45 §4).

---

## 10. Live-verified results (`Reviewer-agent`, 2026-09-27)

Clean PASS — implementation matches this doc, every claim below independently re-verified live (chart binary data decoded and stacked-bar arithmetic re-derived by hand, semantic view DDL re-run live, both new verified queries independently re-queried).

**Priority Queue** (§3): 3 machines rendered, ranked `priority_score` descending — `CNC_MILLING 75.3`, `CNC_BORING 70.3`, `CNC_HORIZONTAL 27.6`. All 3 "survives its own demand?" badges = **Yes**. Stacked-bar segments independently decoded from the Plotly chart's binary data and confirmed to sum to each row's `priority_score` to ~1e-11 (float precision only, not a real discrepancy).

**Forecast OEE** (§4): predicted failure weeks — `CNC_BORING` 2027-01-18, `CNC_MILLING` 2026-11-09, `CNC_HORIZONTAL` 2028-07-03. Post-fix (§4.5 deviation) availability chart: 100% baseline dipping to 97.66% on Caliper's flagged demand-peak-overlap week; Engine Head stayed flat at 100% this run (no overlap for that line in the current 8-week window).

**Semantic view** (§5): `PriorityScore` entity present with the correct primary key/relationship; both new `AI_VERIFIED_QUERIES` independently re-queried live against `cons.fct_priority_score`/`cons.dim_equipment`, correct data confirmed.

**Agent instructions** (§5.6): before/after wording confirmed matching exactly — the capability boundary is correctly scoped ("reporting" a predicted RUL/priority-score value is now fine, "explaining" one is still refused) with no capability creep beyond what §5.6 specifies.

**Row-click hand-off** (§3.5): confirmed minimal and correct; see §3.5's implementation note on the `st.session_state.pop(...)` self-clearing pattern used instead of this doc's originally-suggested get+manual-clear pattern.

---

## 11. Scope extension — Overview page visual enhancements (frozen 2026-09-27, same session)

### 11.0 Rationale for bundling

Both items below were discovered/requested live during manual testing of this story's own build, on this same still-open branch, touching the same file (`1_Overview.py`) SH-48 already modifies for §3.5's row-click hand-off. Neither item shares a data source or invariant with Priority Queue/Forecast OEE — they are bundled here purely on branch/file/session convenience, matching SH-46's own precedent for a mid-flight bundled addition (per `AGENTS.md`'s SDLC-agent convention). The PR description and the consolidated Jira comment (`Jira-Triage-agent`'s job, not this doc's) must flag this section explicitly as a bundled scope extension discovered during manual testing, not silently mixed into SH-48's original Priority Queue/Forecast OEE scope.

### 11.1 OEE trend chart (closes `docs/04-8-LLD.md` §1 gap)

**Why this is in scope, not new scope**: `docs/04-8-LLD.md:21` specifies this chart as part of FR-CC-01 (Overview page), and `docs/05-Epics.md:151` confirms S-APP-1 (`1_Overview.py`'s own story) was explicitly scoped as "Module 8 §0/§1 (**partial**)" — the OEE trend chart was always a known, deferred gap in an already-built story, not net-new scope invented here.

**Data source** — `cons.fct_oee` (confirmed live: `line_name`, `period_week`, `scheduled_hours`, `breakdown_hours`, `availability_pct`, `performance_pct`, `quality_pct`, `oee_pct`; grain = one row per `line_name` x `period_week`). `availability_pct` and `oee_pct` are both stored as 0-1 ratios at the source (same convention already established and fixed once in §4.5 for the Forecast OEE page — convert to 0-100 once, at load time, not per-chart-render).

```python
@st.cache_data(ttl=300)
def load_oee_trend() -> pd.DataFrame:
    conn = get_connection()
    df = conn.query(
        """
        SELECT line_name, period_week, availability_pct, oee_pct
        FROM cons.cons__fct_oee
        ORDER BY line_name, period_week
        """,
        ttl=300,
    )
    df["AVAILABILITY_PCT"] *= 100
    df["OEE_PCT"] *= 100
    return df
```

**Deviation found during build — `PERIOD_WEEK` dtype (reconciled 2026-09-27)**: this sketch assumed `PERIOD_WEEK` comes back from the Snowflake connector already as a `datetime64` column. It does not — it comes back as Python `object`/`date` dtype, which breaks the monthly rollup's `.dt.to_period("M")` call (§ below) at runtime. `Developer-agent`'s fix: an explicit `df["PERIOD_WEEK"] = pd.to_datetime(df["PERIOD_WEEK"])` conversion added inside `load_oee_trend()`, immediately after the query, before the `*= 100` scaling lines above. `Reviewer-agent` confirmed this is the correct, sufficient fix and that no other column in this function needs the same treatment.

**Metric selector**: a small control (e.g. `st.radio(["Availability %", "OEE %"], horizontal=True)`) lets the user switch which of the two LLD-named columns (`availability_pct`/`oee_pct`) the chart plots — one Plotly line per `line_name` either way. Chosen over cramming both metrics onto one chart (denser, harder to read against the future-week caveat below) or picking only one (drops one of the two columns the LLD explicitly names).

**Weekly/monthly-rollup toggle**: `st.segmented_control(["Weekly", "Monthly rollup"])`, placed next to the metric selector — this is the closest native Streamlit primitive to the mockup's `toggle-group` pill buttons (`mockup/index.html:49-52`), visually and behaviorally (single active selection, pill styling). `st.segmented_control` requires Streamlit ≥1.36; `Developer-agent` should confirm the installed version supports it (fallback: `st.radio(horizontal=True)`, functionally equivalent, less visually matched to the mockup).

**Monthly rollup aggregation**: client-side pandas, no second SQL query — the already-cached weekly `load_oee_trend()` result is grouped by `line_name` and calendar month:

```python
monthly_df = (
    weekly_df.assign(period_month=weekly_df["PERIOD_WEEK"].dt.to_period("M").dt.to_timestamp())
    .groupby(["LINE_NAME", "period_month"], as_index=False)[["AVAILABILITY_PCT", "OEE_PCT"]]
    .mean()
)
```

This is a simple **unweighted average of that month's weekly values** — not weighted by `scheduled_hours` (which would be a truer rollup but isn't what the LLD's one-line spec asks for, and adds a second, unstated aggregation rule). State this as an accepted simplification, matching this project's convention of naming simplifications rather than silently picking one (e.g. §4's PM-hours-not-subtracted-from-scheduled-hours simplification in `cons__fct_oee.sql` itself).

**Chart**: `px.line(df, x="PERIOD_WEEK"/"period_month", y=selected_metric, color="LINE_NAME")`, matching `1_Overview.py`'s existing `px.line` sensor-detail convention (§ "Sensor detail" section, `split_trend_and_latest_tick`'s chart call) rather than introducing a different charting idiom.

**Future-week honesty caveat** — `cons.fct_oee` spans 328 rows, 2023-09-25 to 2026-11-09, including 14 weeks after the actual current date (availability ~100% by construction, since no `BREAKDOWN` events are recorded yet for weeks that haven't happened). Confirmed wording (`st.caption`, directly under the chart, same placement convention as the Forecast OEE page's §4.6 captions):

```python
st.caption(
    f"Weeks after {pd.Timestamp.now().normalize():%Y-%m-%d} show availability near 100% "
    "because no breakdown has been recorded for them yet, not because the model "
    "predicts a breakdown-free future -- read future weeks as \"no data yet\", "
    "not as a forecast."
)
```

Anchored on the actual current wall-clock date (`pd.Timestamp.now()`), not `cons.fct_oee`'s own `MAX(period_week)` — the table's rows already extend into the future by construction (the thin-data generator pre-populates weeks that haven't happened yet), so "future" must mean relative to today, not relative to the table's own max row.

### 11.2 Priority-signals chart (visual upgrade to SH-42's "Order-driven priority signals" section)

**Problem being fixed**: the current render (`1_Overview.py`, "Order-driven priority signals" section) computes `line_df.sort_values("ORDER_WEEK").iloc[-1]` — discarding every week except the latest from a query (`load_priority_signals()`) that already pulls multiple historical weeks of `trailing_4wk_avg_order_units` and `avg_anomaly_score` per line. One plain markdown sentence per line is the entire output. No code change to `load_priority_signals()` itself — the query already has what's needed; only the render loop changes.

**Chart** — one small dual-axis line chart per production line (same historical rows `load_priority_signals()` already returns, grouped by `LINE_NAME` exactly as today), showing `trailing_4wk_avg_order_units` and `avg_anomaly_score` trending over `order_week`, so a rising order trend and a rising anomaly trend that actually coincide are visible together — the whole point of this section per the live-testing discovery. Plotly secondary y-axis (`make_subplots(specs=[[{"secondary_y": True}]])`), not scale-normalization — preserves each series' real, readable units (order volume in actual units/week; `avg_anomaly_score` in the model's native decision-function scale, whose exact range isn't a fixed, known constant to normalize against):

**Deviation found during build — `make_subplots` `specs` shape (reconciled 2026-09-27)**: this section originally sketched `specs=[{"secondary_y": True}]` (a flat, single-level list). That is invalid against the installed Plotly version (`plotly==7.0.0`) — `make_subplots`'s `specs` argument must be a 2D list of dicts, one row of dicts per subplot row, even for a single 1x1 subplot grid. `Developer-agent`'s fix: `specs=[[{"secondary_y": True}]]` (the dict wrapped in an inner list). `Reviewer-agent` independently confirmed the original single-level form genuinely raises `ValueError` on this installed version, and that the corrected 2D-list form produces a genuine dual-axis chart (two traces on independently-scaled `y`/`y2` axes) — see §11.6.

```python
from plotly.subplots import make_subplots
import plotly.graph_objects as go

def render_priority_signal_chart(line_df: pd.DataFrame) -> go.Figure:
    line_df = line_df.sort_values("ORDER_WEEK")
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(x=line_df["ORDER_WEEK"], y=line_df["TRAILING_4WK_AVG_ORDER_UNITS"],
                   name="Order volume (trailing 4wk avg)", mode="lines+markers"),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(x=line_df["ORDER_WEEK"], y=line_df["AVG_ANOMALY_SCORE"],
                   name="Avg anomaly score", mode="lines+markers"),
        secondary_y=True,
    )
    fig.update_yaxes(title_text="Order units/wk", secondary_y=False)
    fig.update_yaxes(title_text="Avg anomaly score", secondary_y=True)
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h"))
    return fig
```

Rows with a null `AVG_ANOMALY_SCORE` (weeks with no anomaly data joined, same `LEFT JOIN` the query already has) plot as a gap in that trace, not a zero — Plotly's default `Scatter` behavior on `NaN` already does this correctly, no extra handling needed.

**Metric cards below the chart** — 3 small badges/cards for the latest week's snapshot only (order volume, anomaly score, spare-part lead time), reusing the "Machine health" section's existing `st.metric` + `st.container(border=True)` convention (`1_Overview.py`'s own established card pattern, lines 216-223) rather than inventing a new card component:

```python
latest = line_df.sort_values("ORDER_WEEK").iloc[-1]
card_cols = st.columns(3)
with card_cols[0]:
    with st.container(border=True):
        st.metric("Order volume (4wk avg)", f"{latest['TRAILING_4WK_AVG_ORDER_UNITS']:.0f}/wk")
with card_cols[1]:
    with st.container(border=True):
        if pd.notna(latest["AVG_ANOMALY_SCORE"]):
            st.metric("Avg anomaly score", f"{latest['AVG_ANOMALY_SCORE']:.3f}")
        else:
            st.metric("Avg anomaly score", "No data")
with card_cols[2]:
    with st.container(border=True):
        if pd.notna(latest["MIN_LEAD_TIME_DAYS"]):
            st.metric("Min spare-part lead time", f"{latest['MIN_LEAD_TIME_DAYS']:.0f} days")
        else:
            st.metric("Min spare-part lead time", "No data")
```

This replaces the current `st.markdown(f"**{line_name}**: " + ...)` wall-of-text sentence entirely — the existing header `st.caption` disclosure text (`1_Overview.py:281-286`, "Descriptive context only, not a computed priority score...") is unchanged and still renders above the per-line chart+cards loop. `anomaly_count` (currently shown inline in the text render) is dropped from the top-level cards per the user's 3-card spec; not lost from the query, just no longer surfaced as its own card (see §11.5).

### 11.3 File checklist update (extends §7)

**This extension's own file scope is `1_Overview.py` only** — distinct from the branch's total file scope, which also carries SH-48's original, separately-already-reviewed §5 changes (`scripts/07_post_setup.sql`, `scripts/README.md`). Any PR description or Jira comment covering this branch should frame it that way, not as "only `1_Overview.py` was modified" on the branch as a whole.

**Modified** (in addition to §7's existing `1_Overview.py` entry):
- `oee_command_center_app/pages/1_Overview.py` — add `load_oee_trend()` query function + OEE trend chart section (metric selector, weekly/monthly toggle, future-week caveat, §11.1) between the existing "Machine health" and "Sensor detail" sections (matching the LLD's own §1 ordering: health cards → OEE trend → sensor detail). Replace the "Order-driven priority signals" render loop's `.iloc[-1]`-only text output with the dual-axis chart + 3 metric cards (§11.2). No change to `load_priority_signals()`'s SQL.

**Reads only, no changes** (extends §7's existing list): `cons.cons__fct_oee` (read-only addition; no change to `cons__fct_oee.sql` itself).

### 11.4 Invariants for Reviewer-agent (extends §6)

9. OEE trend chart's future-week caveat (§11.1) must be present, verbatim or materially equivalent, on every render of the chart — anchored on the actual current date (`pd.Timestamp.now()`), not `cons.fct_oee`'s own max `period_week`.
10. Monthly rollup (§11.1) must be computed client-side from the same cached weekly query result — no second SQL call issued for the monthly view, and the aggregation must be a simple unweighted `mean()` of that month's weekly rows (not weighted by `scheduled_hours`, not a re-query with `DATE_TRUNC('month', ...)` server-side).
11. Priority-signals chart (§11.2) must plot the query's actual multi-week historical rows (`load_priority_signals()`'s existing result, ungrouped down to `.iloc[-1]`) — not silently reverted back to latest-snapshot-only rendering.
12. Priority-signals chart's dual-axis assignment must not be swapped: order volume (`trailing_4wk_avg_order_units`) on the primary y-axis, `avg_anomaly_score` on the secondary y-axis, matching §11.2's spec exactly.
13. No change to `cons.fct_oee`'s own SQL (`predictive_maintenance_dbt/models/consumption/cons__fct_oee.sql`) or to `load_priority_signals()`'s SQL — §11 only changes how `1_Overview.py` renders data both queries already return.

### 11.5 Explicitly deferred (extends §8)

- `anomaly_count` (currently shown inline in the pre-upgrade text render) is not promoted to its own 4th metric card — the user's confirmed spec is 3 cards (order volume, anomaly score, spare-part lead time); `anomaly_count` remains available in `load_priority_signals()`'s result but unsurfaced at the top level. No new story exists for adding it back as a 4th card.
- Weighting the monthly OEE rollup by `scheduled_hours` (a truer rollup than the simple unweighted average chosen in §11.1) — deferred as an unstated aggregation rule the one-line LLD spec doesn't ask for; flagged here rather than silently picked.
- Any chart-level click/hover cross-navigation from the priority-signals chart (e.g. clicking a spike to jump to that week's sensor detail) — not requested, not built.

---

### 11.6 Live-verified results (`Reviewer-agent`, 2026-09-27)

Clean PASS — zero code bugs found. Every data claim below independently re-verified live, including hand-checking the monthly rollup on a **different** month than Developer-agent's own check, and independently re-deriving the wall-clock-vs-data-max date distinction behind §11.1's future-week honesty caveat.

**OEE trend chart** (§11.1): 164 weekly points/line × 2 lines. `MAX(period_week) = 2026-11-09`; 14 future weeks confirmed present and correctly showing near-100% availability (not truncated to today's date). The honesty caveat's anchor was independently confirmed to be the real wall-clock `CURRENT_DATE() = 2026-09-27` (via `pd.Timestamp.now()`), genuinely distinct from the table's own `MAX(period_week)`.

**Monthly rollup** (§11.1): hand-checked on two different months by two different agents — Developer-agent checked Caliper / Nov-2023 (98.835062135425% availability); Reviewer-agent, independently, checked Engine Head / Jan-2024 (97.20187516666...% availability, 92.400102533427% OEE). Both exact matches to the rendered chart values.

**Priority-signals chart** (§11.2): confirmed plotting full history, not just the latest week — Caliper 328 points, Engine Head 164 points, both agents independently confirmed against `load_priority_signals()`'s actual row counts.

**Metric cards** (§11.2): cross-checked by Reviewer-agent against a fresh raw query, independent of Developer-agent's own check — Caliper 2745/wk order volume, "No data" anomaly score, 7-day spare-part lead time; Engine Head 966/wk order volume, "No data" anomaly score, 7-day spare-part lead time. Exact match to the rendered cards.

**Unaffected areas**: Machine health cards, Sensor detail section, and the SH-48 row-click hand-off (§3.5) all confirmed genuinely unaffected by this extension.

**Reporting note**: Developer-agent's own final build report described this extension as touching "only `1_Overview.py`" on the branch — imprecise, since the branch's working tree also carries SH-48's original, separately-already-reviewed §5 semantic-view changes (`scripts/07_post_setup.sql`, `scripts/README.md`). Both sets of changes are legitimate and unrelated to each other; §11.3 above states this extension's own file scope explicitly to avoid repeating that imprecision in any future PR description or Jira comment.

