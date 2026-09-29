/* Page 2: Production — Production Planner home.
   §A: Order trend, §B: Future orders, §C: Production batch gap, §D: Machine health impact. */

function renderProduction() {
  renderOrderTrend();
  renderFutureOrders();
  renderBatchGap();
  renderMachineHealthImpact();
}

/* §A: Order trend — historical order volume with rolling average */
function renderOrderTrend() {
  const lines = Object.keys(MOCK.orderHistory).filter((k) => k !== "weeks");
  const series = lines.map((l) => ({ name: l, color: LINE_COLORS[l], data: MOCK.orderHistory[l] }));
  // 4-week rolling average overlay
  const raSeries = lines.map((l) => {
    const data = MOCK.orderHistory[l];
    const ra = data.map((_, i) => {
      const start = Math.max(0, i - 3);
      const slice = data.slice(start, i + 1);
      return Math.round(slice.reduce((s, v) => s + v, 0) / slice.length);
    });
    return { name: `${l} — 4wk avg`, color: LINE_COLORS[l], data: ra, dashed: true };
  });

  document.getElementById("order-trend-chart").innerHTML = lineChartSVG(
    [...series, ...raSeries],
    MOCK.orderHistory.weeks,
    { width: 500, yFormat: (v) => `${Math.round(v)}`, showArea: false }
  );
  document.getElementById("order-trend-legend").innerHTML = legendHTML(series) + legendHTML(raSeries);
}

/* §B: Future orders / demand look-ahead */
function renderFutureOrders() {
  const el = document.getElementById("future-orders");
  const lines = Object.keys(MOCK.orderFuture).filter((k) => k !== "weeks");
  const categories = MOCK.orderFuture.weeks;
  const series = lines.map((l) => ({ name: l, color: LINE_COLORS[l], data: MOCK.orderFuture[l] }));

  el.innerHTML = `
    <div class="chart-wrap">
      ${barChartSVG(categories, series, { width: 500, yFormat: (v) => `${Math.round(v)} units` })}
    </div>
    <div class="legend">${legendHTML(series)}</div>`;
}

/* §C: Production batch gap — diverging horizontal bar chart (judgment call: §10 item 3)
   Uses a diverging horizontal bar per equipment showing surplus/shortfall of RUL vs required run hours. */
function renderBatchGap() {
  const el = document.getElementById("batch-gap");
  const maxRange = 200;

  const rows = MOCK.priorityScores
    .map((r) => {
      const m = machineById(r.equipment_id);
      const gap = r.predicted_rul_hours - r.required_run_hours_next_4wk;
      const pctOfRequired = r.required_run_hours_next_4wk > 0 ? gap / r.required_run_hours_next_4wk : 0;
      let colorClass;
      if (gap < 0) colorClass = "red";
      else if (pctOfRequired < 0.5) colorClass = "yellow";
      else colorClass = "green";
      return { m, gap, pctOfRequired, colorClass, rul: r.predicted_rul_hours, required: r.required_run_hours_next_4wk };
    })
    .sort((a, b) => a.gap - b.gap);

  el.innerHTML = `
    <div class="hbar-chart">
      <div class="hbar-row" style="font-size:11px; color:var(--muted);">
        <div>Equipment</div>
        <div style="display:flex; justify-content:space-between;"><span>Shortfall</span><span>Surplus</span></div>
        <div style="text-align:right;">Gap (hrs)</div>
      </div>
      ${rows
        .map((r) => {
          const absGap = Math.abs(r.gap);
          const pct = Math.min(absGap / maxRange, 1) * 50;
          const isShortfall = r.gap < 0;
          const barColor = r.colorClass === "red" ? "var(--at-risk)" : r.colorClass === "yellow" ? "var(--watch)" : "var(--healthy)";
          const barStyle = isShortfall
            ? `right:50%; width:${pct}%; background:${barColor};`
            : `left:50%; width:${pct}%; background:${barColor};`;
          return `
          <div class="hbar-row">
            <div class="hbar-label">${r.m.name}<small>${r.m.line} · RUL ${r.rul}h / Need ${r.required}h</small></div>
            <div class="hbar-track">
              <div class="hbar-center-line"></div>
              <div class="hbar-fill" style="${barStyle}"></div>
            </div>
            <div class="hbar-value ${isShortfall ? "negative" : "positive"}">${isShortfall ? "" : "+"}${r.gap}h</div>
          </div>`;
        })
        .join("")}
    </div>
    <div class="caveat" style="margin-top:8px;">Predicted failure week is directional, not calibrated (concordance index 0.94, median absolute error ~182h on 5 test rows). Gap values should be treated as indicative, not precise.</div>`;
}

/* §D: Machine health impact on delivery — stacked list sorted by demand pressure */
function renderMachineHealthImpact() {
  const el = document.getElementById("health-impact-cards");
  const sorted = [...MOCK.machines].sort((a, b) => {
    const pa = MOCK.priorityScores.find((p) => p.equipment_id === a.id);
    const pb = MOCK.priorityScores.find((p) => p.equipment_id === b.id);
    return (pb?.demand_pressure || 0) - (pa?.demand_pressure || 0);
  });

  el.innerHTML = sorted
    .map((m) => {
      const ps = MOCK.priorityScores.find((p) => p.equipment_id === m.id);
      return `
      <div class="asset-risk-row">
        <div class="ar-identity">
          <div class="ar-name">${m.name}</div>
          <div class="ar-line">${m.line} line</div>
        </div>
        <div class="ar-stats">
          <div><div class="ar-stat-val">${ps?.demand_pressure || "—"}</div><div class="ar-stat-label">Demand press.</div></div>
          <div><div class="ar-stat-val">${m.rul_hours} hrs</div><div class="ar-stat-label">RUL</div></div>
          <div><div class="ar-stat-val">${m.throughput_units_per_hour}/hr</div><div class="ar-stat-label">Throughput</div></div>
        </div>
        <div class="ar-badge"><span class="badge ${m.status}">${STATUS_LABEL[m.status]}</span></div>
      </div>`;
    })
    .join("");
}
