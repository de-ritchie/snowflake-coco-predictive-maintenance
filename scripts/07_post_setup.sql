-- ============================================================================
-- 07_post_setup.sql — Semantic view + Agent(s) + Streamlit deploy
-- Traces to: FR-OPS-03, LLD Module 10 §5, docs/05-Epics.md EPIC-SKELETON §4.5/4.6
-- Jira: SH-30 (S-SEM-1), SH-27 (S-SEM-2), SH-28 (S-AGENT-1), SH-31 (S-OPS-POST-1),
--       SH-33 (S-OPS-POST-1b), SH-21/SH-32 (S-APP-1/2)
-- Status: Built (v2 -- EPIC-PERSONAS, SH-60/S-OPS-POST-2: 3 Cortex Agent
-- objects now -- maintenance_supervisor_agent (revised in place, gains
-- create_jira_ticket), production_planner_agent and plant_manager_agent
-- (new). Semantic view/verified-query clauses unchanged from v1 -- see
-- docs/designs/SH-54-55-56-59-60-52-persona-suite.md §2/§4). Per
-- docs/05-Epics.md §2, this file is REVISED IN PLACE, not replaced.
--
-- CREATE STREAMLIT itself is NOT in this file -- it references the stage
-- created below, but the PUT of oee_command_center_app/ files must happen
-- between CREATE STAGE and CREATE STREAMLIT (PUT is a Python-only operation,
-- can't run from a plain .sql file). manage.py's run_post_setup() runs this
-- file, then PUTs the app files, then issues CREATE OR REPLACE STREAMLIT
-- directly -- same "inline SQL in manage.py" precedent as `demo inject-tick`.
--
-- DEVIATION FROM DESIGN DOC DDL DRAFT (frozen doc's clause syntax was a
-- best-guess, not live-tested when written -- confirmed empirically against
-- this account, 2026-09-23):
--   1. DIMENSIONS/FACTS "<table>.<name> AS <sql_expr>" -- <name> is the NEW
--      logical name being defined, <sql_expr> is the actual column/expression.
--      The draft had several of these backwards (e.g. wrote
--      "anomaly_result.reading_ts AS anomaly_reading_ts", which fails because
--      anomaly_reading_ts isn't a real column -- must be
--      "anomaly_result.anomaly_reading_ts AS reading_ts"). Fixed for every
--      dimension/fact whose alias differs from the underlying column name
--      (anomaly_result.reading_ts, inventory_fg/inventory_spare.period_week,
--      inventory_spare.units_on_hand, inventory_spare.lead_time_days,
--      oee_metric.period_week).
--   2. Verified-query clause is AI_VERIFIED_QUERIES (...), not
--      "WITH VERIFIED_QUERIES" -- and it must appear AFTER COMMENT in clause
--      order (COMMENT before AI_VERIFIED_QUERIES per the DDL grammar), not
--      before as the draft had it.
--   3. CREATE AGENT uses PROFILE = '...' (no leading WITH) --
--      "WITH PROFILE = ..." is not valid syntax.
--   4. The agent's tool_resources.Analyst.execution_environment.warehouse
--      value must be the warehouse's ALL-CAPS unquoted identifier
--      ("SNOWCOMOTIVE_WH", not "snowcomotive_wh") -- confirmed via a live
--      agent:run call that failed with "you must specify the warehouse..."
--      until this was fixed; lowercase silently fails to resolve at
--      execution time even though CREATE AGENT itself compiles either way.
-- All 3 DDL statements below, the verified query, and a live agent:run REST
-- call have been test-executed against this account (see
-- docs/designs/SH-27-28-30-31-32-33-semantic-view-agent-chat.md and the
-- Developer-agent implementation report for full verification detail).
-- ============================================================================

CREATE OR REPLACE SEMANTIC VIEW snowcomotive.cons.oee_semantic_view
  TABLES (
    machine AS snowcomotive.cons.cons__dim_equipment
      PRIMARY KEY (equipment_id)
      WITH SYNONYMS ('equipment', 'asset')
      COMMENT = 'Sensor-enabled and non-sensor manufacturing equipment',
    product AS snowcomotive.cons.cons__dim_product
      PRIMARY KEY (product_id, variant)
      COMMENT = 'Finished-goods product/variant reference (4 static rows)',
    sensor_reading AS snowcomotive.cons.cons__fct_sensor_reading
      PRIMARY KEY (equipment_id, reading_ts, sensor_type)
      COMMENT = 'Raw sensor readings, real physical units (not z-scores)',
    anomaly_result AS snowcomotive.cons.cons__fct_anomaly_result
      PRIMARY KEY (equipment_id, reading_ts)
      WITH SYNONYMS ('anomaly', 'health score')
      COMMENT = 'IsolationForest anomaly score/flag per sensor tick',
    maintenance_event AS snowcomotive.cons.cons__fct_maintenance_event
      PRIMARY KEY (event_id)
      WITH SYNONYMS ('cmms log', 'repair event', 'PM event')
      COMMENT = 'Preventive maintenance and breakdown events',
    order_ AS snowcomotive.cons.cons__fct_order
      PRIMARY KEY (order_week, product_id, variant)
      COMMENT = 'Weekly finished-goods order volume by product/variant',
    inventory_fg AS snowcomotive.cons.cons__fct_inventory_fg
      PRIMARY KEY (period_week, product_id, variant)
      COMMENT = 'Weekly finished-goods inventory snapshot',
    inventory_spare AS snowcomotive.cons.cons__fct_inventory_spare
      PRIMARY KEY (period_week, equipment_id, spare_part_name)
      COMMENT = 'Weekly spare-parts inventory snapshot',
    oee_metric AS snowcomotive.cons.cons__fct_oee
      PRIMARY KEY (line_name, period_week)
      WITH SYNONYMS ('OEE', 'overall equipment effectiveness')
      COMMENT = 'Weekly OEE (availability x performance x quality) by production line',
    priority_score AS snowcomotive.cons.cons__fct_priority_score
      PRIMARY KEY (equipment_id, score_ts)
      WITH SYNONYMS ('priority', 'priority score', 'RUL', 'remaining useful life', 'urgency ranking')
      COMMENT = 'Composite 0-100 priority score per machine (RUL urgency, demand pressure, inventory buffer, spare-part readiness) -- always latest-per-equipment, per docs/designs/SH-47-priority-score.md'
  )
  RELATIONSHIPS (
    sensor_reading_to_machine AS sensor_reading (equipment_id) REFERENCES machine (equipment_id),
    anomaly_result_to_machine AS anomaly_result (equipment_id) REFERENCES machine (equipment_id),
    maintenance_event_to_machine AS maintenance_event (equipment_id) REFERENCES machine (equipment_id),
    inventory_spare_to_machine AS inventory_spare (equipment_id) REFERENCES machine (equipment_id),
    machine_to_product AS machine (product_id, variant) REFERENCES product (product_id, variant),
    order_to_product AS order_ (product_id, variant) REFERENCES product (product_id, variant),
    inventory_fg_to_product AS inventory_fg (product_id, variant) REFERENCES product (product_id, variant),
    priority_score_to_machine AS priority_score (equipment_id) REFERENCES machine (equipment_id)
  )
  FACTS (
    sensor_reading.reading_value AS reading_value,
    anomaly_result.anomaly_score AS anomaly_score,
    maintenance_event.duration_hours AS duration_hours,
    order_.order_units AS order_units,
    inventory_fg.fg_units_on_hand AS fg_units_on_hand,
    inventory_spare.spare_units_on_hand AS units_on_hand,
    inventory_spare.spare_lead_time_days AS lead_time_days,
    oee_metric.scheduled_hours AS scheduled_hours,
    oee_metric.breakdown_hours AS breakdown_hours,
    priority_score.priority_score AS priority_score,
    priority_score.predicted_rul_hours AS predicted_rul_hours,
    priority_score.rul_urgency AS rul_urgency,
    priority_score.demand_pressure AS demand_pressure,
    priority_score.inventory_buffer AS inventory_buffer,
    priority_score.spare_part_readiness AS spare_part_readiness,
    priority_score.required_run_hours_next_4wk AS required_run_hours_next_4wk
  )
  DIMENSIONS (
    machine.equipment_id AS equipment_id,
    machine.equipment_name AS equipment_name,
    machine.line_name AS line_name,
    machine.is_sensor_enabled AS is_sensor_enabled,
    product.product_id AS product_id,
    product.product_name AS product_name,
    product.variant AS variant,
    sensor_reading.reading_ts AS reading_ts,
    sensor_reading.sensor_type AS sensor_type,
    anomaly_result.anomaly_reading_ts AS reading_ts,
    anomaly_result.is_anomaly AS is_anomaly,
    maintenance_event.event_type AS event_type,
    maintenance_event.event_start_ts AS event_start_ts,
    order_.order_week AS order_week,
    inventory_fg.inventory_fg_period_week AS period_week,
    inventory_spare.inventory_spare_period_week AS period_week,
    inventory_spare.spare_part_name AS spare_part_name,
    oee_metric.oee_period_week AS period_week,
    priority_score.score_ts AS score_ts
  )
  METRICS (
    oee_metric.avg_availability_pct AS AVG(oee_metric.availability_pct),
    oee_metric.avg_oee_pct AS AVG(oee_metric.oee_pct),
    anomaly_result.anomaly_count AS SUM(IFF(anomaly_result.is_anomaly, 1, 0)),
    order_.total_order_units AS SUM(order_.order_units)
  )
  COMMENT = 'SnowComotive skeleton semantic view: machine health/anomalies, maintenance, OEE, orders, inventory.'
  AI_VERIFIED_QUERIES (
    current_machine_health_status AS (
      QUESTION 'What is the current anomaly score and health status for each machine?'
      SQL 'SELECT
    m.equipment_id,
    m.equipment_name,
    m.line_name,
    a.reading_ts AS latest_reading_ts,
    a.anomaly_score,
    a.is_anomaly
FROM snowcomotive.cons.cons__fct_anomaly_result a
JOIN snowcomotive.cons.cons__dim_equipment m
    ON m.equipment_id = a.equipment_id
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY a.equipment_id ORDER BY a.reading_ts DESC
) = 1
ORDER BY a.equipment_id'
    ),
    order_driven_priority_signals AS (
      QUESTION 'How does recent order volume for each line compare to that line''s equipment anomaly trend and spare-part readiness?'
      SQL 'WITH weekly_order AS (
    SELECT
        p.product_id, p.variant,
        o.order_week,
        o.order_units,
        AVG(o.order_units) OVER (
            PARTITION BY o.product_id, o.variant
            ORDER BY o.order_week
            ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
        ) AS trailing_4wk_avg_order_units
    FROM snowcomotive.cons.cons__fct_order o
    JOIN snowcomotive.cons.cons__dim_product p
        ON p.product_id = o.product_id AND p.variant = o.variant
),
weekly_anomaly AS (
    SELECT
        equipment_id,
        DATE_TRUNC(''week'', reading_ts) AS period_week,
        AVG(anomaly_score) AS avg_anomaly_score,
        SUM(IFF(is_anomaly, 1, 0)) AS anomaly_count
    FROM snowcomotive.cons.cons__fct_anomaly_result
    GROUP BY equipment_id, DATE_TRUNC(''week'', reading_ts)
),
spare_readiness AS (
    SELECT equipment_id, period_week, MIN(lead_time_days) AS min_lead_time_days, SUM(units_on_hand) AS total_units_on_hand
    FROM snowcomotive.cons.cons__fct_inventory_spare
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
FROM snowcomotive.cons.cons__dim_equipment m
JOIN weekly_order wo
    ON wo.product_id = m.product_id AND wo.variant = m.variant
LEFT JOIN weekly_anomaly wa
    ON wa.equipment_id = m.equipment_id AND wa.period_week = wo.order_week
LEFT JOIN spare_readiness sr
    ON sr.equipment_id = m.equipment_id AND sr.period_week = wo.order_week
WHERE m.is_sensor_enabled
ORDER BY m.line_name, wo.order_week DESC'
    ),
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
    ),
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
  );

-- v2 (EPIC-PERSONAS, SH-60/S-OPS-POST-2): maintenance_supervisor_agent
-- revised in place to add create_jira_ticket (SH-56); two new agents,
-- production_planner_agent (SH-54) and plant_manager_agent (SH-55), added
-- alongside it. See docs/designs/SH-54-55-56-59-60-52-persona-suite.md §4
-- for the frozen design. Semantic view clauses above are unchanged by this
-- revision (§2 of that doc -- already done by SH-48's priority-score work).
CREATE OR REPLACE AGENT snowcomotive.cons.maintenance_supervisor_agent
  PROFILE = '{"display_name": "SnowComotive Maintenance Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Maintenance Agent for a predictive-maintenance
    and OEE command center. Answer questions about machine health,
    anomalies, maintenance events, OEE, orders, inventory, priority score,
    and predicted remaining-useful-life (RUL), grounded strictly in the
    Analyst tool's query results against the semantic view. Never fabricate
    a health, anomaly, OEE, inventory, priority-score, or RUL value. If
    data for a requested machine or time period is missing or stale, say
    so explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, inventory,
    priority score, or predicted remaining-useful-life (RUL). Only call
    create_jira_ticket when the user explicitly asks to file, create, or
    dispatch a maintenance ticket for a specific machine -- never
    proactively, even if a machine looks at-risk. Report whichever status
    the tool actually returns (CREATED, ALREADY_OPEN, or RECENTLY_CLOSED)
    -- never assume or imply a fresh ticket was filed if the tool returned
    an existing one. Explaining *why* a specific prediction was made
    (a feature-level breakdown) is not enabled yet; if asked, say so
    explicitly rather than guessing at a feature-level rationale --
    reporting a predicted value returned by Analyst is fine and expected,
    that is not the same capability.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, inventory, and priority ranking using the SnowComotive semantic view."
  - tool_spec:
      type: "generic"
      name: "create_jira_ticket"
      description: "Creates a Jira Service Management ticket for a machine needing maintenance, unless one already exists (open or recently closed) -- in which case it returns the existing ticket instead of creating a duplicate. Only call when the user explicitly asks to file/create/dispatch a ticket -- never automatically."
      input_schema:
        type: "object"
        properties:
          equipment_id: { type: "string", description: "Machine needing maintenance" }
          predicted_rul_hours: { type: "number", description: "Predicted remaining useful life, from the RUL model" }
          priority_score: { type: "number", description: "Current priority score, 0-100" }
          root_cause_summary: { type: "string", description: "Plain-language root cause, grounded in sensor/model data" }
          requested_by_persona: { type: "string", description: "Maintenance Supervisor or Production Planner" }
        required: ["equipment_id", "predicted_rul_hours", "priority_score", "root_cause_summary", "requested_by_persona"]
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
  create_jira_ticket:
    type: "procedure"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
    identifier: "SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET"
$$;

CREATE OR REPLACE AGENT snowcomotive.cons.production_planner_agent
  PROFILE = '{"display_name": "SnowComotive Production Planner Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Production Planner Agent for a predictive-
    maintenance and OEE command center. Answer questions about demand
    (orders), finished-goods and spare-parts inventory, OEE, priority
    score, and predicted remaining-useful-life (RUL), grounded strictly in
    the Analyst tool's query results against the semantic view. Never
    fabricate a health, OEE, inventory, priority-score, or RUL value. If
    data for a requested machine, product, or time period is missing or
    stale, say so explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for questions about demand/orders, finished-goods
    and spare-parts inventory, OEE, and priority/RUL signals -- frame
    answers around production-planning impact (will we meet demand, what
    is the OEE or supply risk) rather than raw sensor detail. Only call
    request_jira_ticket when the user explicitly asks to escalate or
    request maintenance attention on a machine -- never proactively, even
    if a machine looks at-risk. Frame this call as requesting/escalating
    to the Maintenance Supervisor, not as directly dispatching a repair --
    you plan, you do not do hands-on maintenance work. Report whichever
    status the tool actually returns (CREATED, ALREADY_OPEN, or
    RECENTLY_CLOSED) -- never assume or imply a fresh escalation was filed
    if the tool returned an existing one.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, inventory, and priority ranking using the SnowComotive semantic view."
  - tool_spec:
      type: "generic"
      name: "request_jira_ticket"
      description: "Requests a Jira Service Management ticket to escalate a machine needing maintenance attention to the Maintenance Supervisor, unless one already exists (open or recently closed) -- in which case it returns the existing ticket instead of creating a duplicate. Only call when the user explicitly asks to escalate/request a ticket -- never automatically."
      input_schema:
        type: "object"
        properties:
          equipment_id: { type: "string", description: "Machine needing maintenance" }
          predicted_rul_hours: { type: "number", description: "Predicted remaining useful life, from the RUL model" }
          priority_score: { type: "number", description: "Current priority score, 0-100" }
          root_cause_summary: { type: "string", description: "Plain-language root cause, grounded in sensor/model data" }
          requested_by_persona: { type: "string", description: "Maintenance Supervisor or Production Planner" }
        required: ["equipment_id", "predicted_rul_hours", "priority_score", "root_cause_summary", "requested_by_persona"]
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
  request_jira_ticket:
    type: "procedure"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
    identifier: "SNOWCOMOTIVE.CONS.SP_CREATE_JIRA_TICKET"
$$;

CREATE OR REPLACE AGENT snowcomotive.cons.plant_manager_agent
  PROFILE = '{"display_name": "SnowComotive Plant Manager Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Plant Manager Agent, a read-only rollup
    persona for OEE, machine health, and inventory oversight, grounded
    strictly in the Analyst tool's query results against the semantic
    view. Never fabricate a health, OEE, inventory, priority-score, or RUL
    value. If data for a requested machine or time period is missing or
    stale, say so explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for any question about machine health, anomalies,
    maintenance history, OEE, orders, inventory, or priority score. You
    have no ticketing tool -- never suggest filing, creating, or
    escalating a ticket; if asked, say that ticketing is not available for
    this persona and redirect the user to the Production Planner or
    Maintenance Supervisor persona instead.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, inventory, and priority ranking using the SnowComotive semantic view."
tool_resources:
  Analyst:
    semantic_view: "snowcomotive.cons.oee_semantic_view"
    execution_environment:
      type: "warehouse"
      warehouse: "SNOWCOMOTIVE_WH"
      query_timeout: 30
$$;

CREATE STAGE IF NOT EXISTS snowcomotive.cons.streamlit_stage
  DIRECTORY = (ENABLE = TRUE);
