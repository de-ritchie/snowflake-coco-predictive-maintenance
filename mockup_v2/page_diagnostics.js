/* Page 3: Diagnostics — Maintenance Supervisor home.
   §A: Line selector, §B: Sensor diagnostics (per machine, expandable), §C: Priority risk score table. */

function renderDiagnostics() {
  renderLineSelector();
  renderPriorityRiskScore();
  renderSensorDiagnostics();
}

/* §A: Line selector — dropdown (st-selectbox), mirrors Plant Dashboard pattern */
function renderLineSelector() {
  const el = document.getElementById("line-selector");
  const options = ["all", "Caliper", "Engine Head"];
  const labels = { all: "All Lines", Caliper: "Caliper", "Engine Head": "Engine Head" };
  el.innerHTML = `<select class="st-selectbox">${options
    .map((opt) => `<option value="${opt}"${opt === state.lineFilter ? " selected" : ""}>${labels[opt]}</option>`)
    .join("")}</select>`;
  el.querySelector("select").addEventListener("change", (e) => {
    state.lineFilter = e.target.value;
    renderDiagnostics();
  });
}

function filteredMachines() {
  return state.lineFilter === "all" ? MOCK.machines : MOCK.machines.filter((m) => m.line === state.lineFilter);
}

/* §B: Sensor diagnostics — per-machine expandable sensor charts */
function renderSensorDiagnostics() {
  const el = document.getElementById("sensor-diagnostics");
  const machines = filteredMachines();

  el.innerHTML = machines
    .map((m) => {
      const isOpen = state.expandedMachine === m.id;
      const series = MOCK.sensorSeries[m.id];
      const xLabels = series.vibration.map((_, i) => i);
      const charts = Object.entries(series)
        .map(([type, data]) => {
          const svg = lineChartSVG(
            [{ name: type, color: SENSOR_COLORS[type], data }],
            xLabels,
            { showXTicks: false, yMin: Math.min(...data) * 0.95, yMax: Math.max(...data) * 1.05, showArea: true }
          );
          return `<div class="sensor-mini">
            <div class="legend"><span class="li"><span class="sw" style="background:${SENSOR_COLORS[type]}"></span>${type}</span></div>
            <div class="chart-wrap">${svg}</div>
          </div>`;
        })
        .join("");

      return `
      <details class="sensor-detail" id="detail-${m.id}" ${isOpen ? "open" : ""}>
        <summary>
          <span class="badge ${m.status}" style="margin-right:8px;">${STATUS_LABEL[m.status]}</span>
          ${m.name} — ${m.line} line — raw sensor detail (vibration / temperature / RPM)
        </summary>
        <div class="sensor-mini-grid">${charts}</div>
      </details>`;
    })
    .join("");

  el.querySelectorAll("details.sensor-detail").forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open) state.expandedMachine = d.id.replace("detail-", "");
    });
  });
}

/* §C: Priority risk score — ranked table + stacked factor bar */
function renderPriorityRiskScore() {
  const el = document.getElementById("priority-table-body");
  const rows = MOCK.priorityScores.filter(
    (r) => state.lineFilter === "all" || machineById(r.equipment_id)?.line === state.lineFilter
  );

  el.innerHTML = rows
    .map((r) => {
      const m = machineById(r.equipment_id);
      const gap = r.predicted_rul_hours - r.required_run_hours_next_4wk;
      const survives = gap > 0;
      return `
      <tr class="row-clickable" data-machine="${r.equipment_id}">
        <td><strong>${m.name}</strong><br><span style="color:var(--muted); font-size:11px;">${m.line} line</span></td>
        <td class="score-cell">${r.priority_score}</td>
        <td>${r.rul_urgency}</td>
        <td>${r.demand_pressure}</td>
        <td>${r.inventory_buffer}</td>
        <td>${r.spare_part_readiness}</td>
        <td>${factorBarHTML(r)}</td>
        <td><span class="badge ${survives ? "healthy" : "at-risk"}">${survives ? "Yes" : "No"}</span></td>
      </tr>`;
    })
    .join("");

  // Row click → expand sensor detail
  el.querySelectorAll("tr.row-clickable").forEach((tr) => {
    tr.addEventListener("click", () => {
      state.expandedMachine = tr.dataset.machine;
      renderSensorDiagnostics();
      setTimeout(() => {
        const detail = document.getElementById(`detail-${tr.dataset.machine}`);
        if (detail) {
          detail.open = true;
          detail.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 60);
    });
  });
}

function factorBarHTML(r) {
  const factors = ["rul_urgency", "demand_pressure", "inventory_buffer", "spare_part_readiness"];
  const total = factors.reduce((s, f) => s + r[f], 0) || 1;
  return `<div class="factor-bar" title="rul_urgency ${r.rul_urgency} (w=0.40) · demand_pressure ${r.demand_pressure} (w=0.25) · inventory_buffer ${r.inventory_buffer} (w=0.20) · spare_part_readiness ${r.spare_part_readiness} (w=0.15)">
    ${factors.map((f) => `<span style="width:${(r[f] / total) * 100}%; background:${FACTOR_COLORS[f]}"></span>`).join("")}
  </div>`;
}
