/* mockup_v2/data.js — Mock data for the 5-page SnowComotive OEE Command Center.
   Grounded in docs/01-BRD.md, docs/04-8-LLD.md, SH-70 design doc.
   Nothing here is queried from Snowflake. */

function genSeries(n, base, amp, drift) {
  const out = [];
  let v = base;
  for (let i = 0; i < n; i++) {
    v += drift + (Math.random() - 0.5) * amp * 0.5;
    out.push(Math.round(v * 100) / 100);
  }
  return out;
}

function healthStatus(priorityScore) {
  if (priorityScore < 40) return "healthy";
  if (priorityScore < 60) return "watch";
  return "at-risk";
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

const MOCK = {
  machines: [
    {
      id: "CNC_BORING_01",
      name: "CNC Boring",
      line: "Caliper",
      product: "Brake Caliper Assembly",
      rul_hours: 96,
      hours_since_last_service: 210,
      throughput_units_per_hour: 18,
    },
    {
      id: "CNC_MILLING_01",
      name: "CNC Milling",
      line: "Caliper",
      product: "Brake Caliper Assembly",
      rul_hours: 340,
      hours_since_last_service: 60,
      throughput_units_per_hour: 22,
    },
    {
      id: "CNC_HORIZONTAL_01",
      name: "CNC Horizontal Machining Center",
      line: "Engine Head",
      product: "Engine Cylinder Head",
      rul_hours: 42,
      hours_since_last_service: 410,
      throughput_units_per_hour: 8,
    },
  ],

  // Financial inputs (SH-70 §5)
  // revenue_per_unit: per-product revenue (§5.1 — calipers $45-65, engine heads $120-180)
  products: {
    "Brake Caliper Assembly": { revenue_per_unit: 55 },
    "Engine Cylinder Head": { revenue_per_unit: 145 },
  },
  // cost_per_hour_of_downtime: fleet-wide blended rate (§5.2 — $2,500/hr industry-typical)
  cost_per_hour_of_downtime: 2500,

  // OEE data — latest week + trailing 12 weeks (stored as 0-1 ratio per invariant #1)
  // availability varies per line; performance & quality are constant dbt vars (plant-wide)
  performancePct: 0.95,
  qualityPct: 0.98,
  oeeLatest: {
    Caliper: { availability_pct: 0.884, prev_availability_pct: 0.902, oee_pct: 0.823, prev_oee_pct: 0.841, scheduled_hours: 120 },
    "Engine Head": { availability_pct: 0.844, prev_availability_pct: 0.837, oee_pct: 0.786, prev_oee_pct: 0.779, scheduled_hours: 80 },
  },
  oeeWeekly: {
    Caliper: genOeeSeries(12, 0.86, -0.003),
    "Engine Head": genOeeSeries(12, 0.81, 0.002),
  },

  // Breakdown hours for financial risk (latest week, per equipment)
  breakdownHours: {
    CNC_BORING_01: 6.2,
    CNC_MILLING_01: 1.1,
    CNC_HORIZONTAL_01: 9.8,
  },

  // Sensor series — 3 types per machine, ~24 ticks (15-min cadence -> ~6h window)
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

  // Priority scores (cons__fct_priority_score)
  // Weights: 0.40 × rul_urgency + 0.25 × demand_pressure + 0.20 × inventory_buffer + 0.15 × spare_part_readiness (invariant #3)
  priorityScores: [
    {
      equipment_id: "CNC_BORING_01",
      rul_urgency: 40,
      demand_pressure: 92,
      inventory_buffer: 38,
      spare_part_readiness: 70,
      priority_score: 0,
      predicted_rul_hours: 96,
      required_run_hours_next_4wk: 134,
    },
    {
      equipment_id: "CNC_HORIZONTAL_01",
      rul_urgency: 65,
      demand_pressure: 34,
      inventory_buffer: 55,
      spare_part_readiness: 60,
      priority_score: 0,
      predicted_rul_hours: 42,
      required_run_hours_next_4wk: 78,
    },
    {
      equipment_id: "CNC_MILLING_01",
      rul_urgency: 45,
      demand_pressure: 92,
      inventory_buffer: 60,
      spare_part_readiness: 80,
      priority_score: 0,
      predicted_rul_hours: 340,
      required_run_hours_next_4wk: 134,
    },
  ],

  // Order data (cons__fct_order) — trailing 12 weeks + 4-week forward
  orderHistory: {
    weeks: ["W-12","W-11","W-10","W-9","W-8","W-7","W-6","W-5","W-4","W-3","W-2","W-1"],
    Caliper:      [780, 800, 810, 830, 845, 860, 870, 880, 900, 920, 950, 960],
    "Engine Head": [550, 540, 560, 570, 580, 590, 585, 595, 600, 610, 600, 615],
  },
  orderFuture: {
    weeks: ["Wk 1", "Wk 2", "Wk 3", "Wk 4"],
    Caliper:      [980, 1020, 1050, 1080],
    "Engine Head": [630, 640, 670, 710],
  },

  // Forecast OEE — 8-week look-ahead (per invariant #4: directional, not calibrated)
  forecast: {
    weeks: ["Wk 1","Wk 2","Wk 3","Wk 4","Wk 5","Wk 6","Wk 7","Wk 8"],
    Caliper:      [0.85, 0.68, 0.71, 0.78, 0.80, 0.81, 0.82, 0.83],
    "Engine Head": [0.80, 0.79, 0.77, 0.75, 0.60, 0.58, 0.63, 0.70],
    riskWindow:  { line: "Caliper",     startWeek: 1, endWeek: 2, label: "CNC Boring failure window × demand peak" },
    riskWindow2: { line: "Engine Head", startWeek: 4, endWeek: 5, label: "CNC Horizontal overdue PM × wk5-8 uptick" },
    demandOrders: {
      Caliper:      [820, 900, 980, 1050, 1080, 1090, 1095, 1100],
      "Engine Head": [600, 600, 605, 610,  640,  700,  740,  770],
    },
  },

  // Personas
  personas: {
    plant_manager: {
      label: "Plant Manager",
      icon: "M",
      color: "#7b6bd6",
      tools: ["Analyst"],
      canTicket: false,
      guardrail: "Read-only rollup persona — no ticketing tool configured; redirects the user to Supervisor or Planner if asked.",
      homePage: "plant_dashboard",
    },
    planner: {
      label: "Production Planner",
      icon: "P",
      color: "#f08a3c",
      tools: ["Analyst", "explain_prediction", "request_jira_ticket"],
      canTicket: true,
      guardrail: "Frames ticket creation as an escalation/request, not a direct dispatch — prioritizes OEE/forecast/demand framing over raw sensor detail.",
      homePage: "production",
    },
    supervisor: {
      label: "Maintenance Supervisor",
      icon: "S",
      color: "#1a9e5c",
      tools: ["Analyst", "explain_prediction", "create_jira_ticket"],
      canTicket: true,
      guardrail: "Only calls create_jira_ticket on explicit user request to file a ticket — never proactively (FR-JR-03).",
      homePage: "diagnostics",
    },
  },

  // Chat history per persona — each entry is a past session with a title, relative time, and a script
  chatHistory: {
    supervisor: [
      {
        id: "sup-h1",
        title: "Why is CNC Boring flagged...",
        time: "Just now",
        scriptKey: "supervisor",  // reuses main chatScript
      },
      {
        id: "sup-h2",
        title: "CNC Horizontal vibration spike",
        time: "3h ago",
        script: [
          { role: "user", text: "CNC Horizontal vibration jumped 18% in the last hour — what's going on?" },
          { role: "agent", text: "Checking sensor diagnostics and the anomaly model output for CNC Horizontal..." },
          { role: "tool", tool: "explain_prediction", detail: "vibration_rolling_1h_z +0.52, rpm_rolling_8h_z −0.29 — sudden vibration increase with RPM decay suggests bearing wear." },
          { role: "agent", text: "The spike is consistent with early-stage bearing wear: vibration z-score jumped to +0.52 while RPM is drifting down. RUL estimate dropped to ~42h. I'd recommend scheduling inspection within the next shift." },
        ],
      },
      {
        id: "sup-h3",
        title: "Weekly PM schedule review",
        time: "Yesterday",
        script: [
          { role: "user", text: "Pull up the priority scores — what should we tackle this week?" },
          { role: "agent", text: "Top priority is CNC Horizontal (score 62) — overdue PM plus rising anomaly score. CNC Boring (score 56) is second, driven by demand pressure on the Caliper line. CNC Milling is healthy and can wait." },
          { role: "user", text: "Got it, schedule CNC Horizontal for Wednesday." },
          { role: "agent", text: "Noted. I can file a ticket if you'd like — just say the word." },
        ],
      },
      {
        id: "sup-h4",
        title: "Spare parts check for Caliper line",
        time: "2 days ago",
        script: [
          { role: "user", text: "Do we have spare bearings and spindle assemblies in stock for the Caliper line machines?" },
          { role: "agent", text: "Spare part readiness for Caliper line: CNC Boring bearings at 70% stock, CNC Milling spindle assemblies at 80%. Both above minimum threshold, but CNC Boring is worth reordering proactively given its current watch status." },
        ],
      },
    ],
    planner: [
      {
        id: "pln-h1",
        title: "CNC Horizontal PM vs Engine Head",
        time: "Just now",
        scriptKey: "planner",
      },
      {
        id: "pln-h2",
        title: "Next 4-week demand forecast",
        time: "5h ago",
        script: [
          { role: "user", text: "What does the 4-week demand look like for both lines?" },
          { role: "agent", text: "Caliper line demand is climbing steadily: 980 → 1,080 units over 4 weeks (+10%). Engine Head is steeper: 630 → 710 (+13%). The Engine Head ramp coincides with CNC Horizontal's predicted failure window — that's the capacity risk." },
        ],
      },
      {
        id: "pln-h3",
        title: "Batch gap analysis for Caliper",
        time: "Yesterday",
        script: [
          { role: "user", text: "How's the batch gap looking for the Caliper line?" },
          { role: "agent", text: "CNC Boring has a shortfall of −38h (96h RUL vs. 134h required over 4 weeks). CNC Milling has a surplus of +206h. Net line position is positive, but CNC Boring is the bottleneck — if it goes down, Milling can't compensate for its specific operations." },
        ],
      },
    ],
    plant_manager: [
      {
        id: "pm-h1",
        title: "Plant-wide OEE rollup",
        time: "Just now",
        scriptKey: "plant_manager",
      },
      {
        id: "pm-h2",
        title: "Month-over-month OEE trend",
        time: "4h ago",
        script: [
          { role: "user", text: "How has plant OEE trended over the last month?" },
          { role: "agent", text: "Plant-wide OEE averaged 81.2% over the trailing 4 weeks. Caliper line dipped from 84.1% to 82.3% (CNC Boring degradation). Engine Head held steady around 78.6%. Overall trend is flat to slightly negative — the Caliper dip is the main concern." },
        ],
      },
      {
        id: "pm-h3",
        title: "Capital expenditure impact question",
        time: "2 days ago",
        script: [
          { role: "user", text: "If we replaced CNC Horizontal, what's the availability upside?" },
          { role: "agent", text: "CNC Horizontal's current availability is ~84.4%. A new machine in the same class typically runs at 95%+ for the first 2 years. That's a ~10.6pp availability gain on the Engine Head line, translating to roughly $14,500/week in recovered revenue at current demand levels." },
        ],
      },
    ],
  },

  // Chat scripts per persona
  chatScripts: {
    supervisor: [
      { role: "user", text: "Why is CNC Boring flagged right now?" },
      { role: "agent", text: "CNC Boring's anomaly score is 0.42 (watch). Let me check the model's feature attribution..." },
      { role: "tool", tool: "explain_prediction", detail: "vibration_rolling_8h_z contributes +0.31, temperature_rolling_1h_z +0.18 — vibration drift is the dominant factor." },
      { role: "agent", text: "The flag is driven mainly by rising 8h-rolling vibration (z = +0.31), with a smaller temperature contribution. RUL model estimates ~96h remaining useful life." },
      { role: "user", text: "File a ticket for it." },
      { role: "tool", tool: "create_jira_ticket", status: "CREATED", detail: "SUP-142 created — Maintenance Supervisor dispatch, priority High." },
      { role: "agent", text: "Done — created ticket SUP-142 (priority High) for CNC Boring vibration drift investigation." },
    ],
    planner: [
      { role: "user", text: "How does CNC Horizontal's overdue PM affect next week's Engine Head availability?" },
      { role: "agent", text: "Engine Head's projected availability drops from ~75% to ~60% starting week 5, overlapping CNC Horizontal's PM slip and the week 5-8 demand uptick." },
      { role: "user", text: "Escalate a ticket request for CNC Horizontal." },
      { role: "tool", tool: "request_jira_ticket", status: "ALREADY_OPEN", detail: "SUP-139 already open (filed 2 days ago by Maintenance Supervisor) — no duplicate created." },
      { role: "agent", text: "There's already an open ticket for this — SUP-139, filed 2 days ago. I did not create a duplicate." },
    ],
    plant_manager: [
      { role: "user", text: "Give me the plant-wide OEE rollup for this week." },
      { role: "agent", text: "Plant OEE: 80.8% (weighted by scheduled hours). Caliper line: 82.3% OEE (availability dip, CNC Boring watch status). Engine Head line: 78.6% OEE, stable. Both within normal band but Caliper is trending down." },
      { role: "user", text: "What's our financial exposure?" },
      { role: "agent", text: "Total plant-level lost revenue this week: ~$42,750 (17.1 breakdown hours × $2,500/hr blended rate). Per-line: Caliper $7,282 (7.3 breakdown hrs × 18-22 units/hr × $55/unit), Engine Head $11,368 (9.8 breakdown hrs × 8 units/hr × $145/unit). Engine Head drives disproportionate dollar loss due to higher per-unit value." },
      { role: "user", text: "Can you file a maintenance ticket for CNC Boring?" },
      { role: "agent", text: "I don't have ticketing access in this read-only rollup view — ask the Maintenance Supervisor or Production Planner persona to file that." },
    ],
  },
};

// Compute priority scores using the real formula weights (invariant #3)
MOCK.priorityScores.forEach((r) => {
  r.priority_score = Math.round(
    r.rul_urgency * 0.40 + r.demand_pressure * 0.25 + r.inventory_buffer * 0.20 + r.spare_part_readiness * 0.15
  );
});
MOCK.priorityScores.sort((a, b) => b.priority_score - a.priority_score);

// Derive machine status + priority_score from computed priorityScores
MOCK.machines.forEach((m) => {
  const ps = MOCK.priorityScores.find((r) => r.equipment_id === m.id);
  m.priority_score = ps ? ps.priority_score : 0;
  m.status = healthStatus(m.priority_score);
});
