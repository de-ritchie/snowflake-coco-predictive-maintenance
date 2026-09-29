/* Page 0: Choose Persona — landing page with 3 persona cards.
   On click, sets persona in state and auto-routes to persona's home page. */

function renderChoosePersona() {
  const el = document.getElementById("persona-cards");
  const descriptions = {
    plant_manager: "Plant-wide OEE rollup, financial risk exposure, forecast availability, and asset health summary.",
    planner: "Order trends, demand vs. capacity gap analysis, and machine health impact on delivery timelines.",
    supervisor: "Sensor diagnostics per production line, priority risk scoring, and maintenance ticket dispatch.",
  };
  const routeLabels = {
    plant_manager: "Plant Overview",
    planner: "Production Planning",
    supervisor: "Risk & Diagnostics",
  };

  el.innerHTML = Object.entries(MOCK.personas)
    .map(
      ([key, p]) => `
      <div class="persona-card" data-persona="${key}">
        <div class="pc-icon" style="background:${p.color}">${p.icon}</div>
        <div class="pc-title">${p.label}</div>
        <div class="pc-desc">${descriptions[key]}</div>
        <div class="pc-route">Opens ${routeLabels[key]} &rarr;</div>
      </div>`
    )
    .join("");

  el.querySelectorAll(".persona-card").forEach((card) => {
    card.addEventListener("click", () => {
      const key = card.dataset.persona;
      state.persona = key;
      renderSidebar();
      showPage(MOCK.personas[key].homePage);
    });
  });
}
