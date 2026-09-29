/* Page 1: Plant Dashboard — Plant Manager home.
   §A: Line filter + 4 KPI cards (OEE, Availability, Performance, Quality)
   §B: Asset Risk Summary
   §C: Financial Exposure (card-based)
   §D: Historical OEE & Predicted Availability (side-by-side) */

function renderPlantDashboard() {
  renderPlantLineFilter();
  renderPlantGranularityToggle();
  renderOeeKpis();
  renderAssetRisk();
  renderFinancialRisk();
  renderOeeTrend();
  renderForecastAvailability();
}

/* ========== Global Line Filter (dropdown) ========== */
function renderPlantLineFilter() {
  const el = document.getElementById("plant-line-selector");
  const lines = ["all", ...Object.keys(MOCK.oeeLatest)];
  const labels = { all: "All Lines", Caliper: "Brake Caliper", "Engine Head": "Engine Head" };
  const current = state.plantLine || "all";
  el.innerHTML = `<select class="st-selectbox">${lines
    .map((l) => `<option value="${l}"${l === current ? " selected" : ""}>${labels[l]}</option>`)
    .join("")}</select>`;
  el.querySelector("select").addEventListener("change", (e) => {
    state.plantLine = e.target.value;
    renderOeeKpis();
    renderAssetRisk();
    renderFinancialRisk();
  });
}

/* ========== Global Granularity Toggle (dropdown) ========== */
function renderPlantGranularityToggle() {
  const el = document.getElementById("plant-granularity-toggle");
  const options = [
    { value: "weekly", label: "Weekly" },
    { value: "monthly", label: "Monthly" },
  ];
  el.innerHTML = `<select class="st-selectbox">${options
    .map((o) => `<option value="${o.value}"${o.value === state.oeeView ? " selected" : ""}>${o.label}</option>`)
    .join("")}</select>`;
  el.querySelector("select").addEventListener("change", (e) => {
    state.oeeView = e.target.value;
    renderOeeTrend();
  });
}

/* §A: 4 KPI cards — OEE, Availability, Performance, Quality */
function renderOeeKpis() {
  const el = document.getElementById("oee-kpis");
  const oee = MOCK.oeeLatest;
  const line = state.plantLine || "all";

  let oeeVal, prevOee, availVal, prevAvail;
  if (line === "all") {
    const totalSch = Object.values(oee).reduce((s, l) => s + l.scheduled_hours, 0);
    oeeVal = Object.values(oee).reduce((s, l) => s + l.oee_pct * l.scheduled_hours, 0) / totalSch;
    prevOee = Object.values(oee).reduce((s, l) => s + l.prev_oee_pct * l.scheduled_hours, 0) / totalSch;
    availVal = Object.values(oee).reduce((s, l) => s + l.availability_pct * l.scheduled_hours, 0) / totalSch;
    prevAvail = Object.values(oee).reduce((s, l) => s + l.prev_availability_pct * l.scheduled_hours, 0) / totalSch;
  } else {
    const d = oee[line];
    oeeVal = d.oee_pct;
    prevOee = d.prev_oee_pct;
    availVal = d.availability_pct;
    prevAvail = d.prev_availability_pct;
  }

  const perfVal = MOCK.performancePct;
  const qualVal = MOCK.qualityPct;

  function kpiCard(label, value, prevValue, colorVar) {
    const delta = value - prevValue;
    const deltaClass = delta >= 0 ? "positive" : "negative";
    const deltaSign = delta >= 0 ? "+" : "";
    const borderStyle = colorVar ? ` style="border-top: 3px solid var(${colorVar})"` : "";
    return `
      <div class="card metric-card"${borderStyle}>
        <div class="m-label">${label}</div>
        <div class="m-value">${(value * 100).toFixed(1)}%</div>
        <div class="m-delta ${deltaClass}">${deltaSign}${(delta * 100).toFixed(1)}pp vs. prior week</div>
      </div>`;
  }

  el.innerHTML =
    kpiCard("OEE", oeeVal, prevOee, "--sf-blue") +
    kpiCard("Availability", availVal, prevAvail, "--healthy") +
    kpiCard("Performance", perfVal, perfVal, "--watch") +
    kpiCard("Quality", qualVal, qualVal, "--sf-navy");
}

/* §B: Asset Risk Summary — filtered by line, stacked vertical list */
function renderAssetRisk() {
  const el = document.getElementById("asset-risk-cards");
  const line = state.plantLine || "all";
  const machines = line === "all" ? MOCK.machines : MOCK.machines.filter((m) => m.line === line);
  el.innerHTML = machines
    .sort((a, b) => b.priority_score - a.priority_score)
    .map(
      (m) => `
      <div class="asset-risk-row">
        <div class="ar-identity">
          <div class="ar-name">${m.name}</div>
          <div class="ar-line">${m.line} line</div>
        </div>
        <div class="ar-stats">
          <div><div class="ar-stat-val">${m.rul_hours} hrs</div><div class="ar-stat-label">RUL</div></div>
          <div><div class="ar-stat-val">${m.hours_since_last_service} hrs</div><div class="ar-stat-label">Since service</div></div>
        </div>
        <div class="ar-badge"><span class="badge ${m.status}">${STATUS_LABEL[m.status]}</span></div>
      </div>`
    )
    .join("");
}

/* §C: Financial Exposure — card-based layout */
function renderFinancialRisk() {
  const el = document.getElementById("financial-risk");
  const line = state.plantLine || "all";

  // Build per-line financials
  const lineData = {};
  MOCK.machines.forEach((m) => {
    if (!lineData[m.line]) lineData[m.line] = { lost: 0, breakdownHrs: 0 };
    const bh = MOCK.breakdownHours[m.id] || 0;
    const rev = MOCK.products[m.product]?.revenue_per_unit || 0;
    lineData[m.line].lost += bh * m.throughput_units_per_hour * rev;
    lineData[m.line].breakdownHrs += bh;
  });

  const totalBreakdownHrs = Object.values(MOCK.breakdownHours).reduce((s, h) => s + h, 0);
  const plantLost = totalBreakdownHrs * MOCK.cost_per_hour_of_downtime;

  if (line === "all") {
    // Show total + per-line as cards
    const lineCards = Object.entries(lineData)
      .map(
        ([name, data]) => `
        <div class="card metric-card" style="border-top: 3px solid ${LINE_COLORS[name]}">
          <div class="m-label">${name} Line Lost Revenue</div>
          <div class="m-value" style="color:var(--at-risk);">$${Math.round(data.lost).toLocaleString()}</div>
          <div class="m-note">${data.breakdownHrs.toFixed(1)}h breakdown &times; throughput &times; unit revenue</div>
        </div>`
      )
      .join("");

    el.innerHTML = `
      <div class="grid-4">
        <div class="card metric-card" style="border-top: 3px solid var(--at-risk)">
          <div class="m-label">Total Lost Revenue (this week)</div>
          <div class="m-value" style="color:var(--at-risk);">$${Math.round(plantLost).toLocaleString()}</div>
          <div class="m-note">${totalBreakdownHrs.toFixed(1)} breakdown hours &times; $${MOCK.cost_per_hour_of_downtime.toLocaleString()}/hr blended rate</div>
        </div>
        ${lineCards}
      </div>
      <p class="caveat">Plant-level uses a blended rate; per-line uses product-specific revenue per unit. These are two different lenses on the same loss.</p>`;
  } else {
    // Single line selected
    const data = lineData[line];
    if (!data) { el.innerHTML = ""; return; }
    el.innerHTML = `
      <div class="grid-4">
        <div class="card metric-card" style="border-top: 3px solid ${LINE_COLORS[line]}">
          <div class="m-label">${line} Line Lost Revenue (this week)</div>
          <div class="m-value" style="color:var(--at-risk);">$${Math.round(data.lost).toLocaleString()}</div>
          <div class="m-note">${data.breakdownHrs.toFixed(1)}h breakdown &times; throughput &times; unit revenue</div>
        </div>
      </div>`;
  }
}

/* §D-left: Historical OEE trend */
function renderOeeTrend() {
  const allLines = Object.keys(MOCK.oeeWeekly);
  let series, labels;
  if (state.oeeView === "weekly") {
    labels = MOCK.oeeWeekly.Caliper.map((_, i) => `W${i + 1}`);
    series = allLines.map((l) => ({ name: l, color: LINE_COLORS[l], data: MOCK.oeeWeekly[l] }));
  } else {
    labels = ["M1", "M2", "M3"];
    series = allLines.map((l) => ({
      name: l, color: LINE_COLORS[l], data: chunkAvg(MOCK.oeeWeekly[l], 4),
    }));
  }

  document.getElementById("oee-trend-chart").innerHTML = lineChartSVG(series, labels, {
    width: 500, yFormat: (v) => `${Math.round(v * 100)}%`, yMin: 0.5, yMax: 1,
  });
  document.getElementById("oee-trend-legend").innerHTML = legendHTML(series);
}

/* §D-right: Predicted Availability (8-week look-ahead) */
function renderForecastAvailability() {
  const lines = Object.keys(MOCK.forecast).filter((k) => LINE_COLORS[k]);
  const series = lines.map((l) => ({ name: `${l} — availability`, color: LINE_COLORS[l], data: MOCK.forecast[l] }));
  const rightSeries = lines.map((l) => ({
    name: `${l} — order demand`, color: LINE_COLORS[l], data: MOCK.forecast.demandOrders[l], dashed: true,
  }));

  document.getElementById("forecast-chart").innerHTML = lineChartSVG(series, MOCK.forecast.weeks, {
    width: 500,
    yFormat: (v) => `${Math.round(v * 100)}%`, yMin: 0.5, yMax: 1,
    riskWindows: [MOCK.forecast.riskWindow, MOCK.forecast.riskWindow2],
    rightSeries,
    rightYFormat: (v) => `${Math.round(v)}u`,
    leftAxisLabel: "Availability %",
    rightAxisLabel: "Order vol.",
  });
  document.getElementById("forecast-legend").innerHTML = legendHTML(series) + legendHTML(rightSeries);
  document.getElementById("forecast-callouts").innerHTML = [MOCK.forecast.riskWindow, MOCK.forecast.riskWindow2]
    .map((w) => `<div class="hint warning">${w.line}: ${w.label} (weeks ${w.startWeek + 1}–${w.endWeek + 1})</div>`)
    .join("");
}
