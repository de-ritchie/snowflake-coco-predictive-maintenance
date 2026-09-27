/* SnowComotive Streamlit mockup — render/interaction logic. Vanilla JS SPA,
   no build step, no external libs (self-contained per html-authoring skill
   guidance — safe to open as a plain local file or later publish as a report). */

const state = {
  persona: "supervisor",
  machineFilter: "all",
  oeeView: "weekly",
  expandedMachine: "CNC_BORING_01",
  activePage: "overview",
  ticking: false,
};

const STATUS_LABEL = { healthy: "Healthy", watch: "Watch", "at-risk": "At risk" };
const FACTOR_COLORS = {
  rul_urgency: "#d1414a",
  demand_pressure: "#29b5e8",
  inventory_buffer: "#7b6bd6",
  spare_part_readiness: "#1a9e5c",
};
const LINE_COLORS = { Caliper: "#29b5e8", "Engine Head": "#f08a3c" };
const SENSOR_COLORS = { vibration: "#d1414a", temperature: "#f08a3c", rpm: "#29b5e8" };

document.addEventListener("DOMContentLoaded", () => {
  renderSidebar();
  renderTopNav();
  renderOverview();
  renderPriorityQueue();
  renderForecast();
  renderChat();
  renderImpact();
  bindStaticControls();
  showPage(state.activePage);
});

/* ---------------- App shell ---------------- */

function bindStaticControls() {
  document.getElementById("tick-btn").addEventListener("click", injectTick);
}

function renderTopNav() {
  document.querySelectorAll(".topnav button").forEach((btn) => {
    btn.addEventListener("click", () => showPage(btn.dataset.page));
  });
}

function showPage(pageId) {
  state.activePage = pageId;
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${pageId}`));
  document.querySelectorAll(".topnav button").forEach((b) => b.classList.toggle("active", b.dataset.page === pageId));
}

function renderSidebar() {
  const list = document.getElementById("persona-list");
  list.innerHTML = "";
  Object.entries(MOCK.personas).forEach(([key, p]) => {
    const el = document.createElement("div");
    el.className = "persona-option" + (state.persona === key ? " active" : "");
    el.innerHTML = `
      <span class="p-name">${p.label}</span>
      <span class="p-tools">${p.tools.join(" · ")}</span>
    `;
    el.addEventListener("click", () => {
      state.persona = key;
      renderSidebar();
      renderChat();
    });
    list.appendChild(el);
  });

  const filter = document.getElementById("machine-filter");
  if (!filter.dataset.bound) {
    filter.innerHTML =
      `<option value="all">All machines</option>` +
      MOCK.machines.map((m) => `<option value="${m.id}">${m.name}</option>`).join("");
    filter.addEventListener("change", (e) => {
      state.machineFilter = e.target.value;
      renderOverview();
      renderPriorityQueue();
      renderForecast();
    });
    filter.dataset.bound = "1";
  }
}

function visibleMachines() {
  return state.machineFilter === "all" ? MOCK.machines : MOCK.machines.filter((m) => m.id === state.machineFilter);
}

/* ---------------- Tick injection (FR-CC-06) ---------------- */

function injectTick() {
  if (state.ticking) return;
  state.ticking = true;
  const btn = document.getElementById("tick-btn");
  btn.disabled = true;
  btn.innerHTML = `<span class="spin"></span> Propagating tick...`;

  setTimeout(() => {
    MOCK.machines.forEach((m) => {
      const delta = (Math.random() - 0.45) * 0.12;
      m.anomaly_score = Math.max(0.02, Math.min(0.95, m.anomaly_score + delta));
      m.rul_hours = Math.max(4, Math.round(m.rul_hours - Math.random() * 6));
      m.status = m.anomaly_score > 0.55 ? "at-risk" : m.anomaly_score > 0.3 ? "watch" : "healthy";
      Object.keys(MOCK.sensorSeries[m.id]).forEach((sensor) => {
        const series = MOCK.sensorSeries[m.id][sensor];
        const last = series[series.length - 1];
        series.shift();
        series.push(Math.round((last + (Math.random() - 0.5) * last * 0.05) * 100) / 100);
      });
    });

    MOCK.priorityScores.forEach((row) => {
      row.rul_urgency = Math.max(5, Math.min(99, Math.round(row.rul_urgency + (Math.random() - 0.5) * 10)));
      row.demand_pressure = Math.max(5, Math.min(99, Math.round(row.demand_pressure + (Math.random() - 0.5) * 6)));
      row.priority_score = Math.round(
        row.rul_urgency * 0.35 + row.demand_pressure * 0.35 + (100 - row.inventory_buffer) * 0.15 + (100 - row.spare_part_readiness) * 0.15
      );
    });
    MOCK.priorityScores.sort((a, b) => b.priority_score - a.priority_score);

    Object.keys(MOCK.oeeWeekly).forEach((line) => {
      const series = MOCK.oeeWeekly[line];
      const last = series[series.length - 1];
      series.shift();
      series.push(Math.max(0.55, Math.min(0.95, Math.round((last + (Math.random() - 0.5) * 0.03) * 1000) / 1000)));
    });

    MOCK.tickSequence += 1;
    renderOverview();
    renderPriorityQueue();
    renderForecast();
    flash("#overview-flash-target");
    flash("#pq-flash-target");

    btn.disabled = false;
    btn.innerHTML = `Inject next tick`;
    state.ticking = false;
  }, 1100);
}

function flash(selector) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.classList.remove("flash");
  requestAnimationFrame(() => el.classList.add("flash"));
}

/* ---------------- Overview page ---------------- */

function renderOverview() {
  const cardsEl = document.getElementById("health-cards");
  cardsEl.innerHTML = visibleMachines()
    .map(
      (m) => `
      <div class="card health-card" id="overview-flash-target">
        <div class="h-top">
          <div>
            <div class="h-name">${m.name}</div>
            <div class="h-line">${m.line} line</div>
          </div>
          <span class="badge ${m.status}">${STATUS_LABEL[m.status]}</span>
        </div>
        <div class="h-score">${m.anomaly_score.toFixed(2)} <span class="unit">anomaly score</span></div>
        <div class="h-meta">Predicted RUL: ${m.rul_hours}h &nbsp;·&nbsp; ${m.hours_since_last_service}h since last service</div>
      </div>`
    )
    .join("");

  renderOeeChart();
  renderSensorDetails();
}

function renderOeeChart() {
  const toggle = document.getElementById("oee-toggle");
  toggle.querySelectorAll("button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === state.oeeView);
    b.onclick = () => {
      state.oeeView = b.dataset.view;
      renderOeeChart();
    };
  });

  const lines = Object.keys(MOCK.oeeWeekly);
  let series, labels;
  if (state.oeeView === "weekly") {
    labels = MOCK.oeeWeekly.Caliper.map((_, i) => `W${i + 1}`);
    series = lines.map((l) => ({ name: l, color: LINE_COLORS[l], data: MOCK.oeeWeekly[l] }));
  } else {
    // monthly rollup: average every 4 weeks
    labels = ["M1", "M2", "M3"];
    series = lines.map((l) => ({
      name: l,
      color: LINE_COLORS[l],
      data: chunkAvg(MOCK.oeeWeekly[l], 4),
    }));
  }
  document.getElementById("oee-chart").innerHTML = lineChartSVG(series, labels, { yFormat: (v) => `${Math.round(v * 100)}%`, yMin: 0.5, yMax: 1 });
  document.getElementById("oee-legend").innerHTML = legendHTML(series);
}

function chunkAvg(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) {
    const chunk = arr.slice(i, i + n);
    out.push(chunk.reduce((a, b) => a + b, 0) / chunk.length);
  }
  return out;
}

function renderSensorDetails() {
  const wrap = document.getElementById("sensor-details");
  wrap.innerHTML = visibleMachines()
    .map((m) => {
      const isOpen = state.expandedMachine === m.id;
      const series = MOCK.sensorSeries[m.id];
      const xLabels = series.vibration.map((_, i) => i);
      // Each sensor gets its own chart with its own y-scale — vibration (~units),
      // temperature (~F/C) and rpm (~thousands) are not comparable on one shared axis.
      const charts = Object.entries(series)
        .map(([type, data]) => {
          const svg = lineChartSVG([{ name: type, color: SENSOR_COLORS[type], data }], xLabels, {
            showXTicks: false,
            yMin: Math.min(...data) * 0.95,
            yMax: Math.max(...data) * 1.05,
          });
          return `<div class="sensor-mini">
            <div class="legend"><span class="li"><span class="sw" style="background:${SENSOR_COLORS[type]}"></span>${type}</span></div>
            <div class="chart-wrap">${svg}</div>
          </div>`;
        })
        .join("");
      return `
      <details class="sensor-detail" id="detail-${m.id}" ${isOpen ? "open" : ""}>
        <summary>${m.name} — raw sensor detail (vibration / temperature / rpm)</summary>
        <div class="sensor-mini-grid">${charts}</div>
      </details>`;
    })
    .join("");

  wrap.querySelectorAll("details.sensor-detail").forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open) state.expandedMachine = d.id.replace("detail-", "");
    });
  });
}

/* ---------------- Priority Queue page ---------------- */

function renderPriorityQueue() {
  const rows = MOCK.priorityScores.filter((r) => state.machineFilter === "all" || r.equipment_id === state.machineFilter);
  const byId = Object.fromEntries(MOCK.machines.map((m) => [m.id, m]));

  document.getElementById("pq-table-body").innerHTML = rows
    .map((r) => {
      const m = byId[r.equipment_id];
      return `
      <tr class="row-clickable" data-machine="${r.equipment_id}" id="pq-flash-target">
        <td><strong>${m.name}</strong><br><span style="color:var(--muted); font-size:11px;">${m.line} line</span></td>
        <td class="score-cell">${r.priority_score}</td>
        <td>${r.rul_urgency}</td>
        <td>${r.demand_pressure}</td>
        <td>${r.inventory_buffer}</td>
        <td>${r.spare_part_readiness}</td>
        <td>${factorBarHTML(r)}</td>
      </tr>`;
    })
    .join("");

  document.querySelectorAll("#pq-table-body tr.row-clickable").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.expandedMachine = tr.dataset.machine;
      showPage("overview");
      renderOverview();
      setTimeout(() => document.getElementById(`detail-${tr.dataset.machine}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 60);
    });
  });

  const topId = rows[0]?.equipment_id;
  const top = byId[topId];
  document.getElementById("pq-hint").textContent = top
    ? `#1 ranked: ${top.name} (${top.line} line) — driven mainly by demand_pressure, not the highest anomaly/RUL urgency. See BRD "priority flip" narrative: business context, not raw failure probability, decides ranking.`
    : "";
}

function factorBarHTML(r) {
  const factors = ["rul_urgency", "demand_pressure", "inventory_buffer", "spare_part_readiness"];
  const total = factors.reduce((s, f) => s + r[f], 0) || 1;
  return `<div class="factor-bar" title="rul_urgency ${r.rul_urgency} · demand_pressure ${r.demand_pressure} · inventory_buffer ${r.inventory_buffer} · spare_part_readiness ${r.spare_part_readiness}">
    ${factors.map((f) => `<span style="width:${(r[f] / total) * 100}%; background:${FACTOR_COLORS[f]}"></span>`).join("")}
  </div>`;
}

/* ---------------- Forecast OEE page ---------------- */

function renderForecast() {
  const lines = Object.keys(MOCK.forecast).filter((k) => LINE_COLORS[k]);
  const activeLines = lines.filter(
    (l) => state.machineFilter === "all" || MOCK.machines.find((m) => m.id === state.machineFilter)?.line === l
  );
  const series = activeLines.map((l) => ({ name: `${l} — availability`, color: LINE_COLORS[l], data: MOCK.forecast[l] }));
  const rightSeries = activeLines.map((l) => ({
    name: `${l} — order demand`,
    color: LINE_COLORS[l],
    data: MOCK.forecast.demandOrders[l],
    dashed: true,
  }));

  document.getElementById("forecast-chart").innerHTML = lineChartSVG(series, MOCK.forecast.weeks, {
    yFormat: (v) => `${Math.round(v * 100)}%`,
    yMin: 0.5,
    yMax: 1,
    riskWindows: [MOCK.forecast.riskWindow, MOCK.forecast.riskWindow2],
    xCount: MOCK.forecast.weeks.length,
    rightSeries,
    rightYFormat: (v) => `${Math.round(v)}u`,
    leftAxisLabel: "Availability %",
    rightAxisLabel: "Order vol.",
  });
  document.getElementById("forecast-legend").innerHTML = legendHTML(series) + legendHTML(rightSeries);
  document.getElementById("forecast-callout").innerHTML = [MOCK.forecast.riskWindow, MOCK.forecast.riskWindow2]
    .map((w) => `<div class="hint">⚠ <strong>${w.line}</strong>: ${w.label} (weeks ${w.startWeek + 1}-${w.endWeek + 1})</div>`)
    .join("");
}

/* ---------------- Agent Chat page (persona-scoped, FR-CC-05) ---------------- */

function renderChat() {
  const persona = MOCK.personas[state.persona];
  const script = MOCK.chatScripts[state.persona];

  document.getElementById("chat-persona-name").textContent = persona.label;

  const ctx = document.getElementById("persona-context-body");
  ctx.innerHTML = `
    <div class="tool-chip-row">
      ${persona.tools.map((t) => `<span class="tool-chip">${t}</span>`).join("")}
      ${!persona.canTicket ? `<span class="tool-chip disabled">create_jira_ticket</span>` : ""}
    </div>
    <p class="guardrail-note">${persona.guardrail}</p>
  `;

  const msgs = document.getElementById("chat-messages");
  msgs.innerHTML = script.map(renderMsg).join("");
  msgs.scrollTop = msgs.scrollHeight;

  const stepBtn = document.getElementById("chat-step-btn");
  stepBtn.onclick = () => replayScript(state.persona);
}

function renderMsg(m) {
  if (m.role === "tool") {
    const statusClass = m.status ? m.status.toLowerCase() : "";
    const statusBadge = m.status ? `<span class="tool-status ${statusClass}">${m.status.replace("_", " ")}</span>` : "";
    return `<div class="msg tool"><span class="tool-name">🔧 ${m.tool}</span>${statusBadge}<div>${m.detail}</div></div>`;
  }
  return `<div class="msg ${m.role}">${m.text}</div>`;
}

function replayScript(personaKey) {
  const script = MOCK.chatScripts[personaKey];
  const msgs = document.getElementById("chat-messages");
  msgs.innerHTML = "";
  let i = 0;
  const step = () => {
    if (i >= script.length) return;
    msgs.insertAdjacentHTML("beforeend", renderMsg(script[i]));
    msgs.scrollTop = msgs.scrollHeight;
    i += 1;
    setTimeout(step, 550);
  };
  step();
}

/* ---------------- Impact Statement page ---------------- */

function renderImpact() {
  const i = MOCK.impact;
  document.getElementById("impact-cards").innerHTML = `
    <div class="card metric-card">
      <div class="m-label">Unplanned downtime reduction</div>
      <div class="m-value">${i.downtime_reduction_pct}%</div>
      <div class="m-note">vs. pre-predictive-maintenance baseline</div>
    </div>
    <div class="card metric-card">
      <div class="m-label">Time-to-detect improvement</div>
      <div class="m-value">${i.time_to_detect_improvement_pct}%</div>
      <div class="m-note">anomaly flagged vs. manual inspection cadence</div>
    </div>
    <div class="card metric-card">
      <div class="m-label">At-risk revenue protected</div>
      <div class="m-value">$${(i.at_risk_revenue_protected_usd / 1000).toFixed(0)}k</div>
      <div class="m-note">avoided line-down exposure, trailing period</div>
    </div>
    <div class="card metric-card">
      <div class="m-label">Concordance index</div>
      <div class="m-value">${i.concordance_index.toFixed(2)}</div>
      <div class="m-note">RUL (AFT) model evaluation, Module 5 §3</div>
    </div>
  `;
}

/* ---------------- Tiny inline-SVG chart helpers (no external libs) ---------------- */

function lineChartSVG(series, xLabels, opts = {}) {
  const hasRight = Array.isArray(opts.rightSeries) && opts.rightSeries.length > 0;
  const W = 640, H = 220, pad = { l: 40, r: hasRight ? 46 : 12, t: 12, b: 24 };
  const innerW = W - pad.l - pad.r, innerH = H - pad.t - pad.b;
  const n = xLabels.length;

  let yMin = opts.yMin, yMax = opts.yMax;
  if (yMin === undefined || yMax === undefined) {
    const allVals = series.flatMap((s) => s.data);
    yMin = opts.perSeriesScale ? Math.min(...allVals) * 0.95 : Math.min(...allVals);
    yMax = opts.perSeriesScale ? Math.max(...allVals) * 1.05 : Math.max(...allVals);
  }
  const xAt = (i) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * innerW);
  const yAt = (v) => pad.t + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;

  let riskRects = "";
  if (opts.riskWindows && opts.xCount) {
    riskRects = opts.riskWindows
      .map((w) => {
        const x1 = xAt(w.startWeek), x2 = xAt(w.endWeek);
        return `<rect x="${x1}" y="${pad.t}" width="${x2 - x1}" height="${innerH}" fill="#d1414a" opacity="0.08"></rect>`;
      })
      .join("");
  }

  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => {
      const y = pad.t + innerH * f;
      const val = yMax - (yMax - yMin) * f;
      const label = opts.yFormat ? opts.yFormat(val) : val.toFixed(1);
      return `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="#eef1f4" stroke-width="1"></line>
              <text x="${pad.l - 6}" y="${y + 3}" font-size="9" fill="#8a94a0" text-anchor="end">${label}</text>`;
    })
    .join("");

  const paths = series
    .map((s) => {
      const d = s.data.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).join(" ");
      const dots = s.data
        .map((v, i) => `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="2.4" fill="${s.color}"></circle>`)
        .join("");
      return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2"></path>${dots}`;
    })
    .join("");

  // Secondary (right) y-axis — independent scale, dashed lines, no fill under the
  // primary axis's zero-line so it reads as a distinct overlay (e.g. demand volume
  // plotted against availability %, Forecast OEE page).
  let rightAxisTicks = "";
  let rightPaths = "";
  if (hasRight) {
    const rAllVals = opts.rightSeries.flatMap((s) => s.data);
    const rMin = opts.rightYMin !== undefined ? opts.rightYMin : Math.min(...rAllVals) * 0.9;
    const rMax = opts.rightYMax !== undefined ? opts.rightYMax : Math.max(...rAllVals) * 1.1;
    const rYAt = (v) => pad.t + innerH - ((v - rMin) / (rMax - rMin || 1)) * innerH;

    rightAxisTicks = [0, 0.25, 0.5, 0.75, 1]
      .map((f) => {
        const y = pad.t + innerH * f;
        const val = rMax - (rMax - rMin) * f;
        const label = opts.rightYFormat ? opts.rightYFormat(val) : Math.round(val);
        return `<text x="${W - pad.r + 6}" y="${y + 3}" font-size="9" fill="#8a94a0" text-anchor="start">${label}</text>`;
      })
      .join("");

    rightPaths = opts.rightSeries
      .map((s) => {
        const d = s.data.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${rYAt(v).toFixed(1)}`).join(" ");
        const dots = s.data
          .map((v, i) => `<circle cx="${xAt(i).toFixed(1)}" cy="${rYAt(v).toFixed(1)}" r="2" fill="${s.color}"></circle>`)
          .join("");
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6" stroke-dasharray="5,3" opacity="0.75"></path>${dots}`;
      })
      .join("");
  }

  const xTicks =
    opts.showXTicks === false
      ? ""
      : xLabels
          .map((lbl, i) => {
            if (n > 8 && i % Math.ceil(n / 8) !== 0 && i !== n - 1) return "";
            return `<text x="${xAt(i).toFixed(1)}" y="${H - 6}" font-size="9" fill="#8a94a0" text-anchor="middle">${lbl}</text>`;
          })
          .join("");

  const axisLabels = hasRight
    ? `<text x="${pad.l}" y="8" font-size="9" fill="#5b6572" text-anchor="middle">${opts.leftAxisLabel || ""}</text>
       <text x="${W - pad.r}" y="8" font-size="9" fill="#5b6572" text-anchor="end">${opts.rightAxisLabel || ""}</text>`
    : "";

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridLines}${riskRects}${rightAxisTicks}${axisLabels}${paths}${rightPaths}${xTicks}
  </svg>`;
}

function legendHTML(series) {
  return series
    .map(
      (s) =>
        `<span class="li"><span class="sw${s.dashed ? " sw-dashed" : ""}" style="background:${s.dashed ? "none" : s.color}; border-color:${s.color}"></span>${s.name}</span>`
    )
    .join("");
}
