# Business Requirements Document (BRD)
## SnowComotive — Predictive Maintenance & OEE Command Center

Status: Draft v0.1 — pending user validation
Owner: Chiraj Jayaraman
Built with: Cortex Code (CoCo) — CLI + Desktop, across planning/development/execution/testing

---

## 1. Business Problem

SnowComotive is an automotive parts manufacturer producing two product lines:

- **Brake Caliper** (EV and ICE variants)
- **Engine Head** (EV and ICE variants — ICE-only in practice, included for completeness of the demand-shift narrative)

Today, SnowComotive operates on **preventive maintenance only** (fixed time-based service schedules) plus reactive repair after breakdowns. There is no predictive maintenance capability. This creates three compounding problems:

1. **Unplanned downtime is discovered, not anticipated.** OT sensor data (vibration, temperature, RPM) from shop-floor machines sits in isolation from ERP/CMMS records, so early degradation signals are never correlated with maintenance history, spare-parts readiness, or business impact.
2. **Maintenance prioritization ignores business context.** When multiple machines show risk, maintenance is dispatched by machine criticality or dollar-value-at-risk alone — not by which failure would hurt the business most *right now*, given firm near-term orders and inventory buffers.
3. **Root cause investigation is manual and slow.** Diagnosing *why* a machine is trending toward failure requires a technician to manually cross-reference sensor trends, maintenance logs, and machine history — there is no natural-language interface to accelerate this.

## 2. Industry / Domain Context

- **Domain:** Discrete manufacturing, automotive components (machining-heavy: CNC boring, CNC horizontal machining centers, CNC milling).
- **Market dynamic driving the story:** EV adoption is increasing demand for Brake Calipers while reducing demand for ICE Engine Heads. This demand shift must influence maintenance prioritization, not just raw failure probability — a machine with a *lower* failure probability but feeding *high, growing* demand can be more business-critical than a machine with a *higher* failure probability feeding *declining* demand.
- **Maturity baseline (explicit assumption):** No predictive maintenance existed prior to this solution. Only time-based preventive maintenance (PM) and reactive breakdown repair existed. This is the "before" state against which impact is measured.
- **Demand signal realism:** In real automotive supply chains, OEM demand arrives via EDI into the supplier's ERP (SAP SD) — as a longer-range forecast (EDI 830 Planning Schedule, 1-12 month horizon) and as firm, near-term commitments (EDI 862 Shipping Schedule, days-to-weeks horizon). Predicted Remaining Useful Life (RUL) operates in a days-to-weeks horizon (FR-FS-04), so **only the EDI 862-style firm near-term order signal is modeled** — a 1-12 month forecast has no bearing on which machine to prioritize this week. EDI 830 is noted as a future extension (e.g., strategic capacity planning), out of MVP scope. This MVP represents the firm-order signal as synthetic weekly order data landed directly in Snowflake — actual EDI integration is out of scope; system definitions are covered in the FRD's Systems Landscape.

## 3. Target Users / Personas

Three personas, each with a tailored Cortex Agent (same underlying semantic model, different tool access and framing):

| Persona | Role | Primary Need | Agent Capabilities |
|---|---|---|---|
| **Maintenance Supervisor** | Owns machine health & repair dispatch | Triage alerts, understand root cause in plain language, act immediately | Root-cause explanation, sensor/health drill-down, **create Jira Service ticket** (direct dispatch action) |
| **Production Planner** | Owns line throughput & order fulfillment | Understand OEE trend/forecast, see demand-and-inventory-aware prioritization across both lines, justify why one machine is prioritized over another | OEE explanation, prioritization reasoning, forecast queries, ticket **request/escalation** (same tool, different framing — not a direct dispatch) |
| **Plant Manager** | Owns plant-level outcomes, executive visibility | High-level rollup of OEE, risk, and $ impact across the plant — no operational action needed | Read-only summarization and Q&A over OEE/risk/impact — **no ticketing tool** |

Rationale for capping at 3 personas: each represents a genuinely distinct decision context (operational action, tactical planning, executive oversight) rather than a cosmetic UI variant — keeps the demo focused while still demonstrating persona-differentiated agent design.

## 4. Current Pain Point → Target Improvement

| Pain Point Today (preventive-only baseline) | Target State (this solution) |
|---|---|
| Machines serviced on fixed calendar schedule regardless of actual condition, or repaired reactively after failure | Machines serviced based on predicted Remaining Useful Life (RUL), ahead of failure |
| Sensor data reviewed manually, in isolation from maintenance/ERP context | Sensor + maintenance + demand + inventory data converged in one semantic layer, queryable in natural language |
| Prioritization across competing maintenance needs is ad hoc / dollar-value-only | Prioritization is an explainable, multi-factor score (RUL urgency + demand pressure + inventory buffer + spare-part readiness) |
| Root cause investigation requires manual log/trend correlation by a technician | Natural-language root-cause chat, persona-specific, with on-demand action (ticket creation) |
| No structured view of how machine health will affect near-future OEE | Forecast OEE view showing projected Availability impact against firm near-term orders, by line |

## 5. Scope

### 5.1 Value Stream (In Scope)

**Line A — Brake Caliper** (casting → machined → assembled)
1. Casting receipt & inspection
2. **CNC Boring** — *sensor-enabled*
3. **CNC Milling** — *sensor-enabled*
4. Drilling/Tapping
5. Deburr & Wash
6. Leak/Pressure Test
7. Coating/Painting
8. Assembly
9. Final Test & Packaging

**Line B — Engine Head** (casting → machined → valve-train fit → tested)
1. Casting receipt & inspection
2. **CNC Horizontal Machining Center** — *sensor-enabled*
3. Valve seat/guide press-fit
4. Deburr & Wash
5. Hydro/Leak Test
6. Final Inspection & Packaging

Three sensor-enabled machines total: **CNC Boring**, **CNC Milling** (both Caliper line — enables *within-line* prioritization contrast), **CNC Horizontal Machining Center** (Engine Head line — enables *across-line* contrast).

Non-instrumented stages exist in the value stream (for OEE/routing completeness and order-impact context) but do not generate sensor telemetry in this MVP.

### 5.2 In Scope (MVP/Demo)

- Synthetic historical data generation (3 years ago → 30 days ago): sensor telemetry (uniform 15-minute cadence — not per-second, to manage data volume and credit usage), CMMS maintenance logs (with technician notes as a text column, not separate documents), working/holiday calendar, and **firm sales orders spanning the full 3-year window plus an 8-week look-ahead** (one continuous generated series — orders are the root cause, machine utilization is derived from them, not the reverse; see FRD FR-DG-07/08).
- Data lands in Snowflake as **Parquet files in a Snowflake Stage** (not CSV/dbt seeds), loaded into Raw tables via `COPY INTO`.
- Simulated near-real-time data: last 30 days of sensor data injected via 15-minute-refresh Dynamic Tables (no Kafka/Snowpipe Streaming — explicitly out of scope).
- dbt-on-Snowflake pipeline, layered as **Raw / Standardized / Consumption** (not Bronze/Silver/Gold): Raw is an unmodified copy of source data, Standardized cleans and joins but never aggregates, Consumption builds dimension/fact tables, aggregates, and feature tables.
- Feature engineering (`FEAST` schema, built entirely in dbt — no external Feature Store product): one reusable macro implements rolling-window aggregation and baseline normalization, invoked from two dbt models that differ only in materialization — an always-fresh dynamic table for inference, and a frozen per-training-run table for reproducible model training. Point-in-time-correct labeling (RUL's censored survival labels) is a training-only spine table, joined against the frozen feature snapshot.
- A single trained **multivariate anomaly detection model** (`snowflake.ml.modeling.ensemble.IsolationForest`, a Snowpark ML Modeling estimator) — takes baseline-normalized vibration, temperature, and RPM as joint input features across all 3 sensor-enabled machines in one model, so it learns combined sensor patterns directly rather than following hand-set SQL rules or requiring a separate per-sensor model. Registered and versioned in the Snowflake Model Registry (a supported built-in model type) alongside the RUL model, and retrained periodically (e.g. weekly) as an ordinary versioned refresh — not a forced full-replace. Output (anomaly flag + continuous anomaly score) feeds the dashboard and is used as an input feature to the RUL model.
- RUL prediction model: XGBoost with `survival:aft` objective (handles right-censored machines correctly), registered with metrics in Snowflake Model Registry.
- Decoupled training vs. inference pipelines; inference writes predictions back to Snowflake.
- Explainable multi-factor prioritization score (RUL urgency, demand, inventory, spare parts) combining predictions with ERP-like order/inventory data.
- Semantic view + Cortex Analyst/Agent for natural-language root cause and OEE/prioritization Q&A.
- 3 persona-specific agents with differentiated tool access.
- Streamlit OEE Command Center: historical OEE (weekly/monthly), live alert/priority queue, forecast OEE view, agent chat per persona.
- On-demand Jira Service ticket creation from the Maintenance Supervisor agent (MCP/API integration).
- Jira Kanban board for this project's own Epic/Story/Task tracking, with a human-gated, CoCo-agent-assisted SDLC (Design → Develop → Review → Document, manually invoked per story).
- Environment lifecycle automation: a setup script (role/warehouse/database/stage/data generation/upload/Jira registry), a pipeline run step (dbt Raw→Standardized→Consumption, with model training run between two dbt passes since inference tables reference the trained models by name), a post-setup script (agent + Streamlit deployment), a demo script (near-real-time tick injection), and a teardown script — this is the primary evidence of the CoCo "Execution" lifecycle phase for judging.

### 5.3 Explicitly Out of Scope (MVP/Demo)

- Real IoT/SCADA integration, Kafka, Snowpipe Streaming (simulated via scheduled Dynamic Table refresh instead).
- Importing historical Jira Service tickets (assumed to already exist as CMMS log history instead; the agent only *creates new* tickets going forward).
- Optimization beyond a greedy weighted-score prioritization (Monte Carlo simulation / constrained optimization explicitly deferred to future state, not built now).
- True survival-analysis libraries (e.g., `lifelines` Cox PH) — the AFT objective inside XGBoost is used instead, as a lighter-weight way to handle censoring correctly. Note: Snowpark ML's high-level XGBRegressor does not expose AFT's censored-label interface, so training/inference runs as a Snowpark Python Stored Procedure using the native `xgboost` package directly — this is a required implementation detail, not an alternative in scope/out of scope.
- A standalone unstructured-document corpus for maintenance manuals (replaced by a technician-notes text column on CMMS logs; document parsing noted as a future/stretch extension only).
- Sensors beyond vibration, temperature, RPM (e.g., acoustic, current/load) — noted as future extension.
- Real payroll/shift-crew variability, multi-plant/multi-site scope — single plant, single site.

## 6. Core Assumptions

1. No predictive maintenance existed before this solution — only preventive (fixed-schedule) and reactive repair.
2. Working calendar: 5-day work week, 3×8-hour shifts (24 operating hours/day), generic holiday calendar (~10 holidays/year), plus a small residual random unplanned idle chance (~2-3% per working day) not tied to explainable causes — on top of the order-derived schedule (assumption 9).
3. Quality % and Performance % (of OEE) are fixed synthetic constants per line; **Availability % is the only fluctuating OEE driver** in this MVP, since it's the one predictive maintenance directly affects.
4. Sensor health decays over usage (Weibull-shaped degradation + noise), is machine-specific baselined (different mean/variance per machine type), and is only partially restored on service (70–95%, not 100%) — restoration % differs between broad preventive maintenance (recovers more, touches more parts) and targeted breakdown repair (recovers primarily the failed component's signal). *When* that service happens is itself constrained — see assumptions 13-15.
5. ~10–15% of breakdowns are deliberately generated as "unexplainable" (no preceding sensor precursor) to reflect real-world failure modes (electrical, control, human error) and to avoid an artificially perfect (and therefore non-credible) model.
6. "Orders affected" by downtime is computed as a derived metric (`downtime_hours × line_throughput_rate`), not tracked via literal order IDs — avoids unnecessary complexity while preserving the business-impact narrative.
7. All data is synthetic and generated to be referentially consistent; no production or real personal data is used at any stage.
8. RUL is modeled in operating hours (usage-based, matching the decay model) and translated to calendar-day estimates via trailing utilization rate for dashboard readability.
9. Machine utilization is **derived from orders**, not the reverse: a customer places a firm order (CoEVheeler for calipers, CoCoCars for engine heads) → required machine-hours are computed from that order (smoothed over a trailing few weeks, so the schedule responds to a sustained shift, not one week's order noise) → capped at available capacity (working days × 24 operating hours, no overtime modeled for MVP) → that becomes the machine's actual operating schedule, which then drives wear accumulation (assumption 4). A small residual idle chance (~2-3% per working day) applies on top, independent of orders, representing ordinary logistics/staffing variance. This correctly reflects real automotive JIT/EDI-862 supplier relationships (the order causes the production, not the other way around). Note: this by itself would make Caliper wear *and* demand rise together, and Engine Head fall together on both — which would eliminate the demo's counter-intuitive tension entirely (see assumptions 13-15 for the mechanism that restores it: maintenance neglect, not wear-rate, is what makes Engine Head's risk non-obvious).
10. No cold-start/warm-up transient is modeled after idle periods (holidays, weekends, unplanned idle days) — in reality a machine would start at a more optimal reading and ramp up over its first ~15-60 minutes running. This is a known simplification (secondary effect, doesn't change the long-term wear/RUL story) noted here as a future improvement rather than built for MVP.
11. Sales order volume is the independently generated, root input — spanning the full 3-year history plus an 8-week look-ahead as one continuous series (linear trend, no compounding growth). The 8-week look-ahead follows a two-beat, gradual-ramp structure, not an instant step: weeks 1-4 ramp Caliper demand up while Engine Head stays flat; weeks 5-8 add a renewed Engine Head uptick on top of sustained Caliper demand (assumption 15). It lands the same way as every other source (Parquet → Stage → `COPY INTO`), not via a dbt derivation — only *utilization* (assumption 9) is derived, and that derivation lives inside the generator's simulation loop, not as a table anyone queries.
12. RUL forecasting never requires simulating future sensor data — the model outputs a single "predicted hours from now" value from currently-available features. The demo's "this machine might break during the demand spike" narrative is a comparison between that prediction (translated to a calendar date) and the independently-generated order look-ahead window, not a joint simulation of wear-under-demand.
13. **Maintenance crew capacity is shared and limited plant-wide**, not per-machine: at most 1 PM job per week (weekday) across *all* machines (sensor-enabled and not), plus 1 additional PM job on designated maintenance weekends (biweekly, ~26/year — zero Availability impact, since weekends were never scheduled production time). Breakdowns bypass this cap entirely — a stopped line always gets immediate attention regardless of the weekly slot's status.
14. **Crew capacity is also contested by the plant's other, non-instrumented machines** (Casting, Drilling, Deburr, Coating, Assembly, etc. — real stages in the value stream, BRD §5.1, but out of sensor/data scope). Rather than fully simulating those machines' own wear, each week carries a background ~20-30% chance the single crew slot is already consumed elsewhere in the plant before any of the 3 monitored machines are even considered — a lightweight stand-in for "the rest of the plant exists and also needs attention," not a full simulation.
15. **When multiple monitored machines are due for PM in the same available slot, priority goes to whichever product line has higher current-week order volume.** This — not a difference in per-hour wear rate (assumption 9 explicitly keeps that neutral) — is the mechanism behind the demo's counter-intuitive priority moment: Caliper gets serviced on schedule (crew prioritizes the busy line) but re-wears fast given how much it runs; Engine Head's moderate wear rate compounds over a longer *unattended* stretch because its PM keeps losing the priority contest. Two different reasons for concern, not one machine being objectively worse than the other. **Accepted fallback**: this mechanism is stochastic and not guaranteed to land a sufficiently dramatic outcome on every generation run — if it doesn't, the fallback is a documented, post-generation hand-patch of Engine Head's trailing-30-day maintenance/sensor data (not a change to the simulation logic itself) — see LLD Module 2 §10 for the exact procedure.

## 7. Demo Narrative (centerpiece story for judging)

**"The EV Demand Shift Flips the Priority Queue"**

1. **Baseline view** — Command Center opens on a calm state: all 3 machines show healthy status, OEE trending normally by line.
2. **Demand shift signal** — The 8-week order look-ahead shows a two-beat pattern: weeks 1-4 carry a ramping EV Brake Caliper order spike with Engine Head flat (continuing its existing decline); weeks 5-8 show Engine Head picking up too, alongside sustained Caliper demand. This is what the crew-priority mechanism (assumption 15) responds to.
3. **Priority flip** — The prioritization queue re-ranks: **CNC Boring (Caliper line)** rises to the top — not because Engine Head is objectively more failure-prone (it isn't, by design; assumption 9 keeps per-hour wear rate neutral), but because Caliper's maintenance has been kept current (crew attention follows demand) while running hard, versus **CNC Horizontal (Engine Head)**, whose last PM slipped past its due date because the crew was tied up with Caliper — moderate wear rate, but a longer unattended stretch. Two distinct risk stories, not a reversal of an objective ranking. This is the "intuitive vs. counter-intuitive" moment the solution is built to demonstrate: business context decides which of two comparably-plausible risks gets attention first.
4. **Root cause drill-down** — Maintenance Supervisor persona opens agent chat: *"Why is CNC Boring the top priority right now?"* Agent explains in plain language: predicted RUL, contributing sensor signature (e.g., bearing wear pattern), demand pressure, and inventory buffer — grounded in the semantic view, not a canned answer.
5. **Action on demand** — Supervisor asks the agent to file a maintenance ticket; a Jira Service ticket is created live via the agent's tool call.
6. **Live update moment** — A 15-minute data tick is manually triggered on stage; the health score / chart visibly updates, demonstrating the near-real-time Dynamic Table refresh (compressed for demo timing, not faked).
7. **Forecast payoff** — Switch to Production Planner persona: Forecast OEE view shows the Caliper line's projected Availability dip next week if the flagged machine isn't serviced, and flags that Engine Head's own overdue PM plus its weeks 5-8 demand uptick means it's next in line — closing the loop from sensor signal → business impact across the full look-ahead, not just a single moment.

## 8. Impact Statement (Measurable Outcomes)

To be computed against the synthetic historical baseline (preventive-only) vs. the predictive-maintenance-enabled simulation:

1. **Unplanned downtime reduction (%)** — compare unplanned downtime hours in a held-out historical period under the preventive-only baseline vs. what predictive intervention (acting on model-flagged high-risk windows) would have avoided.
2. **Time-to-detect improvement** — mean lead time between the model's first high-risk flag and the actual historical failure/breakdown event, vs. zero lead time under the preventive-only (fixed schedule) or reactive baseline.
3. **$ at-risk revenue protected** — dollar value of orders_at_risk (`downtime_hours × throughput_rate × unit_margin`) that demand-and-inventory-aware prioritization protects, compared to a naive dollar-value-only or failure-probability-only prioritization baseline.
4. **Model accuracy (concordance index)** — concordance index of the AFT survival model on a held-out, time-based test split, reported alongside MAE on the uncensored subset.

**Scalability / beyond-demo potential (for submission's Impact Statement):**
- Additional machines/sensors can be onboarded incrementally (same Raw/Standardized/Consumption pipeline, same feature store pattern) without re-architecting.
- Greedy prioritization score is explicitly a placeholder for a stated V2: Monte Carlo simulation over demand/inventory uncertainty + constrained optimization across both lines.
- The $-at-risk Impact Statement metric (§8) uses the standard `downtime_hours × throughput_rate × unit_margin` formula — a defensible MVP baseline, but it implicitly assumes every downtime hour would have been sold. A stated V2 refinement caps this against actual demand that period (`MIN(downtime_hours × throughput_rate, that period's order volume)`), moving beyond textbook risk toward what's actually at stake given real demand.
- The human-gated, CoCo-agent-assisted SDLC (Design/Develop/Review/Document agents) used to build this solution is itself reusable for future Snowflake projects beyond this hackathon.
- Persona/agent pattern (shared semantic view, differentiated tool access) generalizes to other command-center use cases beyond manufacturing.

## 9. Success Criteria for MVP/Demo

- All 3 personas can hold a natural-language conversation grounded in the semantic view and get correct, explainable answers.
- The demo narrative (Section 7) runs end-to-end live, including the on-stage manual data-tick trigger and live Jira ticket creation.
- RUL model is trained, tested on a time-based split, and registered with metrics in the Model Registry.
- Prioritization queue visibly re-ranks in response to demand-forecast changes, not just raw failure probability.
- Impact Statement metrics (Section 8) are computed from actual pipeline output, not hardcoded.
- Evidence of CoCo usage exists across planning (this document), development (dbt/pipeline/model/agent build), execution (scheduled tasks/Dynamic Table runs), and testing/validation (model test set, agent verified queries) — as required by judging criteria.

## 10. Open Items — resolved by FRD/HLD/LLD

All items originally deferred here are now specified across FRD Sections A-I and LLD Modules 1-11 ([04-0-LLD.md](04-0-LLD.md) index) — dbt model/table schemas (Module 1), semantic view mapping (Module 6), agent tool specs (Module 7), Jira mechanics (Module 9), Streamlit layout (Module 8), SDLC skill design (Module 11), and the data generation algorithm (Module 2). The few remaining genuinely open items are build-time validation/tuning, not design gaps — tracked per-module in each LLD file's own "Deferred to build time" section (e.g., model explainability actual test results, Module 5 §4, is the most build-blocking of these).
