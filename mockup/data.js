/* SnowComotive Streamlit mockup — mock data only.
   Grounded in docs/01-BRD.md (machines/lines/demo narrative), docs/04-8-LLD.md
   (page contents), docs/04-7-LLD.md (persona tools), docs/03-HLD.md §5 (priority
   formula). Nothing here is queried from Snowflake — see README.md. */

const MOCK = {
  machines: [
    {
      id: "CNC_BORING_01",
      name: "CNC Boring",
      line: "Caliper",
      status: "watch",
      anomaly_score: 0.42,
      rul_hours: 96,
      hours_since_last_service: 210,
    },
    {
      id: "CNC_MILLING_01",
      name: "CNC Milling",
      line: "Caliper",
      status: "healthy",
      anomaly_score: 0.17,
      rul_hours: 340,
      hours_since_last_service: 60,
    },
    {
      id: "CNC_HORIZONTAL_01",
      name: "CNC Horizontal Machining Center",
      line: "Engine Head",
      status: "at-risk",
      anomaly_score: 0.64,
      rul_hours: 42,
      hours_since_last_service: 410,
    },
  ],

  // 3 sensor types per machine, ~24 ticks (15-min cadence -> ~6h window) of raw reading_value
  sensorSeries: {
    CNC_BORING_01: {
      vibration: genSeries(24, 2.4, 0.35, 0.015),
      temperature: genSeries(24, 68, 3, 0.4),
      rpm: genSeries(24, 1800, 40, -1.5),
    },
    CNC_MILLING_01: {
      vibration: genSeries(24, 1.9, 0.2, 0.002),
      temperature: genSeries(24, 61, 2, 0.05),
      rpm: genSeries(24, 2100, 30, 0.2),
    },
    CNC_HORIZONTAL_01: {
      vibration: genSeries(24, 3.1, 0.5, 0.06),
      temperature: genSeries(24, 79, 4, 0.9),
      rpm: genSeries(24, 1500, 60, -3.2),
    },
  },

  // cons.fct_oee — weekly, by line, 12 weeks trailing
  oeeWeekly: {
    Caliper: genOeeSeries(12, 0.86, -0.01),
    "Engine Head": genOeeSeries(12, 0.81, 0.003),
  },

  // cons.fct_priority_score — 1 row per machine, current tick
  // Demo narrative (BRD assumption 15 / §7 beat 3): CNC Boring ranks #1 not because
  // it's objectively more failure-prone, but because Caliper-line demand pressure
  // dominates the formula, while CNC Horizontal's higher rul_urgency is offset by
  // lower demand_pressure right now.
  priorityScores: [
    {
      equipment_id: "CNC_BORING_01",
      rul_urgency: 55,
      demand_pressure: 92,
      inventory_buffer: 38,
      spare_part_readiness: 70,
      priority_score: 82,
    },
    {
      equipment_id: "CNC_HORIZONTAL_01",
      rul_urgency: 88,
      demand_pressure: 34,
      inventory_buffer: 55,
      spare_part_readiness: 60,
      priority_score: 68,
    },
    {
      equipment_id: "CNC_MILLING_01",
      rul_urgency: 20,
      demand_pressure: 92,
      inventory_buffer: 60,
      spare_part_readiness: 80,
      priority_score: 31,
    },
  ],

  // Forecast OEE — 8-week look-ahead, projected availability per line (FR-CC-03)
  // Caliper dips next week if CNC Boring isn't serviced; Engine Head degrades from
  // week 5 as its own overdue PM + renewed demand uptick (BRD assumption 11) bite.
  forecast: {
    weeks: Array.from({ length: 8 }, (_, i) => `Wk ${i + 1}`),
    Caliper: [0.85, 0.68, 0.71, 0.78, 0.8, 0.81, 0.82, 0.83],
    "Engine Head": [0.8, 0.79, 0.77, 0.75, 0.6, 0.58, 0.63, 0.7],
    riskWindow: { line: "Caliper", startWeek: 1, endWeek: 2, label: "CNC Boring failure window x demand peak" },
    riskWindow2: { line: "Engine Head", startWeek: 4, endWeek: 5, label: "CNC Horizontal overdue PM x wk5-8 uptick" },
    // cons.fct_order look-ahead volume (BRD assumption 11/FR-DG-07's two-beat structure):
    // weeks 1-4 ramp Caliper demand while Engine Head stays flat; weeks 5-8 add a
    // renewed Engine Head uptick on top of sustained Caliper demand. Units/week.
    demandOrders: {
      Caliper: [820, 900, 980, 1050, 1080, 1090, 1095, 1100],
      "Engine Head": [600, 600, 605, 610, 640, 700, 740, 770],
    },
  },

  // Impact Statement (FR-CC-07, BRD §8) — normally computed from pipeline/model output
  impact: {
    downtime_reduction_pct: 23,
    time_to_detect_improvement_pct: 61,
    at_risk_revenue_protected_usd: 184000,
    concordance_index: 0.78,
  },

  // Personas (Module 7 LLD) — tools + framing differ, semantic view is shared
  personas: {
    supervisor: {
      label: "Maintenance Supervisor",
      tools: ["Analyst", "explain_prediction", "create_jira_ticket"],
      ticketVerb: "create_jira_ticket",
      ticketFraming: "dispatch",
      canTicket: true,
      guardrail:
        "Only calls create_jira_ticket on explicit user request to file a ticket — never proactively (FR-JR-03).",
    },
    planner: {
      label: "Production Planner",
      tools: ["Analyst", "explain_prediction", "request_jira_ticket"],
      ticketVerb: "request_jira_ticket",
      ticketFraming: "escalation",
      canTicket: true,
      guardrail:
        "Frames ticket creation as an escalation/request, not a direct dispatch — prioritizes OEE/forecast/demand framing over raw sensor detail.",
    },
    plant_manager: {
      label: "Plant Manager",
      tools: ["Analyst"],
      ticketVerb: null,
      ticketFraming: null,
      canTicket: false,
      guardrail:
        "Read-only rollup persona — no ticketing tool configured; redirects the user to Supervisor or Planner if asked.",
    },
  },

  // Scripted chat transcripts per persona (illustrates tool-call transparency, FR-CC-05 / FR-JR-04)
  chatScripts: {
    supervisor: [
      { role: "user", text: "Why is CNC Boring flagged right now?" },
      { role: "agent", text: "CNC Boring's anomaly score is 0.42 (watch). Checking the model's feature attribution..." },
      {
        role: "tool",
        tool: "explain_prediction",
        detail: "vibration_rolling_8h_z contributes +0.31, temperature_rolling_1h_z +0.18 — vibration drift is the dominant factor.",
      },
      {
        role: "agent",
        text: "The flag is driven mainly by rising 8h-rolling vibration (z = +0.31), with a smaller temperature contribution. RUL model estimates ~96h remaining useful life.",
      },
      { role: "user", text: "File a ticket for it." },
      {
        role: "tool",
        tool: "create_jira_ticket",
        status: "CREATED",
        detail: "SH-MAINT-142 created — Maintenance Supervisor dispatch, priority High.",
      },
      { role: "agent", text: "Done — created ticket SH-MAINT-142 (priority High) for CNC Boring." },
    ],
    planner: [
      { role: "user", text: "How does CNC Horizontal's overdue PM affect next week's Engine Head availability?" },
      {
        role: "agent",
        text: "Engine Head's projected availability drops from ~75% to ~60% starting week 5, overlapping CNC Horizontal's PM slip and the week 5-8 demand uptick.",
      },
      { role: "user", text: "Escalate a ticket request for CNC Horizontal." },
      {
        role: "tool",
        tool: "request_jira_ticket",
        status: "ALREADY_OPEN",
        detail: "SH-MAINT-139 already open (filed 2 days ago by Maintenance Supervisor) — no duplicate created.",
      },
      {
        role: "agent",
        text: "There's already an open ticket for this — SH-MAINT-139, filed 2 days ago. I did not create a duplicate.",
      },
    ],
    plant_manager: [
      { role: "user", text: "Give me the plant-wide OEE rollup for this week." },
      {
        role: "agent",
        text: "Caliper line: 79% OEE (availability dip, CNC Boring watch status). Engine Head line: 78% OEE, stable. Both within normal band.",
      },
      { role: "user", text: "Can you file a maintenance ticket for CNC Boring?" },
      {
        role: "agent",
        text: "I don't have ticketing access in this read-only rollup view — ask the Maintenance Supervisor or Production Planner persona to file that.",
      },
    ],
  },

  tickSequence: 0,
};

function genSeries(n, base, amp, drift) {
  const out = [];
  let v = base;
  for (let i = 0; i < n; i++) {
    v += drift + (Math.random() - 0.5) * amp * 0.5;
    out.push(Math.round(v * 100) / 100);
  }
  return out;
}

function genOeeSeries(n, base, drift) {
  const out = [];
  let v = base;
  for (let i = 0; i < n; i++) {
    v += drift + (Math.random() - 0.5) * 0.02;
    out.push(Math.round(Math.max(0.5, Math.min(0.95, v)) * 1000) / 1000);
  }
  return out;
}
