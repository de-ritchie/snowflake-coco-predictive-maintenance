-- ============================================================================
-- 07_post_setup.sql — Semantic view + Agent(s) + Streamlit deploy
-- Traces to: FR-OPS-03, LLD Module 10 §5, docs/05-Epics.md EPIC-SKELETON §4.5/4.6
-- Jira: SH-30 (S-SEM-1), SH-27 (S-SEM-2), SH-28 (S-AGENT-1), SH-31 (S-OPS-POST-1),
--       SH-33 (S-OPS-POST-1b), SH-21/SH-32 (S-APP-1/2)
-- Status: Built (v1 -- skeleton scope: 8-entity semantic view, 1 verified
-- query, 1 Analyst-only agent, Streamlit stage). Per docs/05-Epics.md §2, this
-- file gets REVISED IN PLACE to v2 in EPIC-PERSONAS (S-OPS-POST-2, SH-60) once
-- the 3-persona expansion happens -- not replaced with a new file.
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
      COMMENT = 'Weekly OEE (availability x performance x quality) by production line'
  )
  RELATIONSHIPS (
    sensor_reading_to_machine AS sensor_reading (equipment_id) REFERENCES machine (equipment_id),
    anomaly_result_to_machine AS anomaly_result (equipment_id) REFERENCES machine (equipment_id),
    maintenance_event_to_machine AS maintenance_event (equipment_id) REFERENCES machine (equipment_id),
    inventory_spare_to_machine AS inventory_spare (equipment_id) REFERENCES machine (equipment_id),
    machine_to_product AS machine (product_id, variant) REFERENCES product (product_id, variant),
    order_to_product AS order_ (product_id, variant) REFERENCES product (product_id, variant),
    inventory_fg_to_product AS inventory_fg (product_id, variant) REFERENCES product (product_id, variant)
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
    oee_metric.breakdown_hours AS breakdown_hours
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
    oee_metric.oee_period_week AS period_week
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
    )
  );

CREATE OR REPLACE AGENT snowcomotive.cons.maintenance_supervisor_agent
  PROFILE = '{"display_name": "SnowComotive Maintenance Agent"}'
  FROM SPECIFICATION $$
models:
  orchestration: auto
instructions:
  response: >
    You are the SnowComotive Maintenance Agent for a predictive-maintenance
    and OEE command center. Answer questions about machine health,
    anomalies, maintenance events, OEE, orders, and inventory, grounded
    strictly in the Analyst tool's query results against the semantic view.
    Never fabricate a health, anomaly, OEE, or inventory value. If data for
    a requested machine or time period is missing or stale, say so
    explicitly rather than guessing.
  orchestration: >
    Use the Analyst tool for any question about machine health, sensor
    readings, anomalies, maintenance history, OEE, orders, or inventory.
    This is currently the only tool available -- do not claim to be able to
    create tickets or explain model predictions; if asked, say those
    capabilities are not enabled yet.
tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst"
      description: "Answers questions about machine health, anomalies, maintenance events, OEE, orders, and inventory using the SnowComotive semantic view."
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
