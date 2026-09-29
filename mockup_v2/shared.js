/* mockup_v2/shared.js — Shared state, sidebar, page routing, SVG chart helpers. */

const STATUS_LABEL = { healthy: "Healthy", watch: "Watch", "at-risk": "At Risk" };
const LINE_COLORS = { Caliper: "#29b5e8", "Engine Head": "#f08a3c" };
const SENSOR_COLORS = { vibration: "#d1414a", temperature: "#f08a3c", rpm: "#29b5e8" };
const FACTOR_COLORS = {
  rul_urgency: "#d1414a",
  demand_pressure: "#29b5e8",
  inventory_buffer: "#7b6bd6",
  spare_part_readiness: "#1a9e5c",
};
const FACTOR_WEIGHTS = { rul_urgency: 0.40, demand_pressure: 0.25, inventory_buffer: 0.20, spare_part_readiness: 0.15 };

const state = {
  persona: null,
  activePage: "choose_persona",
  oeeView: "weekly",
  lineFilter: "all",
  plantLine: "all",
  expandedMachine: null,
};

function machineById(id) {
  return MOCK.machines.find((m) => m.id === id);
}

/* ========== Page Routing ========== */

function showPage(pageId) {
  state.activePage = pageId;
  document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${pageId}`));
  document.querySelectorAll(".sidebar-nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === pageId));
  if (pageId === "chat") renderChat();
}

/* ========== Sidebar ========== */

function renderSidebar() {
  const indicator = document.getElementById("persona-indicator");
  if (state.persona) {
    const p = MOCK.personas[state.persona];
    indicator.innerHTML = `Viewing as: <strong>${p.label}</strong>`;
    indicator.style.display = "block";
  } else {
    indicator.style.display = "none";
  }
}

function initSidebar() {
  renderSidebar();
  document.querySelectorAll(".sidebar-nav button").forEach((btn) => {
    btn.addEventListener("click", () => showPage(btn.dataset.page));
  });
}

/* ========== SVG Chart Helpers ========== */

function lineChartSVG(series, xLabels, opts = {}) {
  const hasRight = Array.isArray(opts.rightSeries) && opts.rightSeries.length > 0;
  const W = opts.width || 640, H = opts.height || 220;
  const pad = { l: 44, r: hasRight ? 48 : 14, t: 14, b: 26 };
  const innerW = W - pad.l - pad.r, innerH = H - pad.t - pad.b;
  const n = xLabels.length;

  let yMin = opts.yMin, yMax = opts.yMax;
  if (yMin === undefined || yMax === undefined) {
    const allVals = series.flatMap((s) => s.data);
    yMin = yMin !== undefined ? yMin : Math.min(...allVals) * 0.95;
    yMax = yMax !== undefined ? yMax : Math.max(...allVals) * 1.05;
  }
  const xAt = (i) => pad.l + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const yAt = (v) => pad.t + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;

  let riskRects = "";
  if (opts.riskWindows) {
    riskRects = opts.riskWindows
      .map((w) => {
        const x1 = xAt(w.startWeek), x2 = xAt(w.endWeek);
        return `<rect x="${x1}" y="${pad.t}" width="${x2 - x1}" height="${innerH}" fill="#d1414a" opacity="0.08" rx="3"></rect>`;
      })
      .join("");
  }

  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => {
      const y = pad.t + innerH * f;
      const val = yMax - (yMax - yMin) * f;
      const label = opts.yFormat ? opts.yFormat(val) : val.toFixed(1);
      return `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="#eef1f4" stroke-width="1"/>
              <text x="${pad.l - 6}" y="${y + 3}" font-size="9" fill="#8a94a0" text-anchor="end">${label}</text>`;
    })
    .join("");

  const paths = series
    .map((s) => {
      const d = s.data.map((v, i) => `${i === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)}`).join(" ");
      const areaD = d + ` L${xAt(s.data.length - 1).toFixed(1)},${yAt(yMin).toFixed(1)} L${xAt(0).toFixed(1)},${yAt(yMin).toFixed(1)} Z`;
      const dots = s.data
        .map((v, i) => `<circle cx="${xAt(i).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="2.5" fill="${s.color}"/>`)
        .join("");
      const area = opts.showArea !== false ? `<path d="${areaD}" fill="${s.color}" opacity="0.06"/>` : "";
      const dash = s.dashed ? ' stroke-dasharray="5,3"' : "";
      return `${area}<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2"${dash}/>${dots}`;
    })
    .join("");

  let rightAxisTicks = "", rightPaths = "";
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
          .map((v, i) => `<circle cx="${xAt(i).toFixed(1)}" cy="${rYAt(v).toFixed(1)}" r="2" fill="${s.color}"/>`)
          .join("");
        return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="1.6" stroke-dasharray="5,3" opacity="0.7"/>${dots}`;
      })
      .join("");
  }

  const xTicks =
    opts.showXTicks === false
      ? ""
      : xLabels
          .map((lbl, i) => {
            if (n > 10 && i % Math.ceil(n / 8) !== 0 && i !== n - 1) return "";
            return `<text x="${xAt(i).toFixed(1)}" y="${H - 4}" font-size="9" fill="#8a94a0" text-anchor="middle">${lbl}</text>`;
          })
          .join("");

  const axisLabels = hasRight
    ? `<text x="${pad.l}" y="9" font-size="9" fill="#5b6572" text-anchor="start">${opts.leftAxisLabel || ""}</text>
       <text x="${W - pad.r}" y="9" font-size="9" fill="#5b6572" text-anchor="end">${opts.rightAxisLabel || ""}</text>`
    : "";

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridLines}${riskRects}${axisLabels}${paths}${rightAxisTicks}${rightPaths}${xTicks}
  </svg>`;
}

function barChartSVG(categories, values, opts = {}) {
  const W = opts.width || 640, H = opts.height || 200;
  const pad = { l: 44, r: 14, t: 14, b: 40 };
  const innerW = W - pad.l - pad.r, innerH = H - pad.t - pad.b;
  const n = categories.length;
  const barW = Math.min(40, innerW / n * 0.6);
  const gap = (innerW - barW * n) / (n + 1);

  const maxVal = opts.yMax || Math.max(...values.flatMap((s) => s.data)) * 1.1;
  const yAt = (v) => pad.t + innerH - (v / maxVal) * innerH;

  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => {
      const y = pad.t + innerH * f;
      const val = maxVal * (1 - f);
      const label = opts.yFormat ? opts.yFormat(val) : Math.round(val);
      return `<line x1="${pad.l}" y1="${y}" x2="${W - pad.r}" y2="${y}" stroke="#eef1f4" stroke-width="1"/>
              <text x="${pad.l - 6}" y="${y + 3}" font-size="9" fill="#8a94a0" text-anchor="end">${label}</text>`;
    })
    .join("");

  const seriesCount = values.length;
  const groupBarW = barW / seriesCount;

  const bars = values
    .map((s, si) =>
      s.data
        .map((v, i) => {
          const x = pad.l + gap + i * (barW + gap) + si * groupBarW;
          const h = (v / maxVal) * innerH;
          return `<rect x="${x}" y="${yAt(v)}" width="${groupBarW - 1}" height="${h}" fill="${s.color}" rx="2" opacity="0.85"/>`;
        })
        .join("")
    )
    .join("");

  const xTicks = categories
    .map((lbl, i) => {
      const x = pad.l + gap + i * (barW + gap) + barW / 2;
      return `<text x="${x}" y="${H - 6}" font-size="9" fill="#8a94a0" text-anchor="middle">${lbl}</text>`;
    })
    .join("");

  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">
    ${gridLines}${bars}${xTicks}
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

function chunkAvg(arr, n) {
  const out = [];
  for (let i = 0; i < arr.length; i += n) {
    const chunk = arr.slice(i, i + n);
    out.push(chunk.reduce((a, b) => a + b, 0) / chunk.length);
  }
  return out;
}

function formatDollars(v) {
  if (v >= 1000) return "$" + (v / 1000).toFixed(1) + "k";
  return "$" + Math.round(v).toLocaleString();
}

/* ========== Init ========== */

document.addEventListener("DOMContentLoaded", () => {
  initSidebar();
  renderChoosePersona();
  renderPlantDashboard();
  renderProduction();
  renderDiagnostics();
  renderChat();
  showPage(state.activePage);
});
