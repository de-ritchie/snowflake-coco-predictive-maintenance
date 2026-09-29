/* Page 4: Chat — persona-aware agent chat with tool-call transparency.
   Left history panel, center chat with tool bar above input. */

let _chatActiveHistoryId = null;

function renderChat() {
  const personaKey = state.persona || "supervisor";
  const persona = MOCK.personas[personaKey];

  document.getElementById("chat-persona-name").textContent = persona.label;

  renderChatHistory(personaKey);
  renderToolBar(personaKey);

  // Load the first history entry (current session) by default
  const history = MOCK.chatHistory[personaKey] || [];
  if (history.length > 0) {
    selectChatHistory(personaKey, history[0].id);
  } else {
    document.getElementById("chat-messages").innerHTML = "";
    _chatActiveHistoryId = null;
  }

  document.getElementById("chat-new-btn").onclick = () => {
    _chatActiveHistoryId = null;
    document.getElementById("chat-messages").innerHTML = "";
    document.querySelectorAll(".chat-history-item").forEach((el) => el.classList.remove("active"));
  };

  const stepBtn = document.getElementById("chat-step-btn");
  stepBtn.onclick = () => {
    const script = getActiveScript(personaKey);
    if (script) replayChatScript(script);
  };
}

function renderChatHistory(personaKey) {
  const list = document.getElementById("chat-history-list");
  const history = MOCK.chatHistory[personaKey] || [];
  list.innerHTML = history
    .map(
      (h) =>
        `<div class="chat-history-item" data-id="${h.id}">
          <span class="ch-title">${h.title}</span>
          <span class="ch-time">${h.time}</span>
        </div>`
    )
    .join("");

  list.querySelectorAll(".chat-history-item").forEach((el) => {
    el.addEventListener("click", () => selectChatHistory(personaKey, el.dataset.id));
  });
}

function selectChatHistory(personaKey, historyId) {
  _chatActiveHistoryId = historyId;
  document.querySelectorAll(".chat-history-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === historyId);
  });
  const script = getScriptForHistory(personaKey, historyId);
  const msgs = document.getElementById("chat-messages");
  msgs.innerHTML = script.map(renderChatMsg).join("");
  msgs.scrollTop = msgs.scrollHeight;
}

function getScriptForHistory(personaKey, historyId) {
  const history = MOCK.chatHistory[personaKey] || [];
  const entry = history.find((h) => h.id === historyId);
  if (!entry) return [];
  if (entry.scriptKey) return MOCK.chatScripts[entry.scriptKey] || [];
  return entry.script || [];
}

function getActiveScript(personaKey) {
  if (_chatActiveHistoryId) {
    return getScriptForHistory(personaKey, _chatActiveHistoryId);
  }
  return MOCK.chatScripts[personaKey];
}

function renderToolBar(personaKey) {
  const persona = MOCK.personas[personaKey];
  const bar = document.getElementById("chat-tool-bar");

  const allTools = [...persona.tools];
  if (!persona.canTicket) {
    allTools.push("__disabled__create_jira_ticket");
  }

  bar.innerHTML = `
    <div class="tool-chip-row" id="tool-chip-row">
      ${allTools
        .map((t) => {
          const isDisabled = t.startsWith("__disabled__");
          const name = isDisabled ? t.replace("__disabled__", "") : t;
          return `<span class="tool-chip${isDisabled ? " disabled" : ""}" data-tool-name="${name}">${name}</span>`;
        })
        .join("")}
    </div>
    <p class="guardrail-note">${persona.guardrail}</p>`;

  requestAnimationFrame(() => computeToolOverflow());
}

function computeToolOverflow() {
  const row = document.getElementById("tool-chip-row");
  if (!row) return;

  // Remove any existing overflow chip
  const existing = row.querySelector(".overflow-chip");
  if (existing) existing.remove();

  // Show all chips to measure
  const chips = Array.from(row.querySelectorAll(".tool-chip"));
  chips.forEach((c) => (c.style.display = ""));

  if (chips.length === 0) return;

  const rowWidth = row.clientWidth;
  const gap = 6;
  const overflowReserve = 46; // space for the +N chip

  // Measure each chip's width (including gap for non-first chips)
  const chipWidths = chips.map((c, i) => c.offsetWidth + (i > 0 ? gap : 0));

  // If everything fits, nothing to do
  const totalWidth = chipWidths.reduce((a, b) => a + b, 0);
  if (totalWidth <= rowWidth) return;

  // Find how many chips fit alongside the +N indicator
  let usedWidth = 0;
  let fitCount = 0;
  for (let i = 0; i < chips.length; i++) {
    const needed = usedWidth + chipWidths[i] + (i < chips.length - 1 ? overflowReserve : 0);
    if (needed > rowWidth) break;
    usedWidth += chipWidths[i];
    fitCount++;
  }

  // Enforce a floor: always show at least 1 chip, prefer 2 if the persona has 2+
  const MIN_VISIBLE = chips.length >= 2 ? 2 : 1;
  if (fitCount < MIN_VISIBLE) fitCount = MIN_VISIBLE;

  // If the floor covers all chips, no overflow chip needed
  if (fitCount >= chips.length) return;

  const overflowCount = chips.length - fitCount;
  for (let i = fitCount; i < chips.length; i++) {
    chips[i].style.display = "none";
  }
  const overflowChip = document.createElement("span");
  overflowChip.className = "tool-chip overflow-chip";
  overflowChip.textContent = `+${overflowCount}`;
  row.appendChild(overflowChip);
}

window.addEventListener("resize", () => {
  if (state.activePage === "chat") {
    computeToolOverflow();
  }
});

function renderChatMsg(m) {
  if (m.role === "tool") {
    const statusClass = m.status ? m.status.toLowerCase() : "";
    const statusBadge = m.status ? `<span class="tool-status ${statusClass}">${m.status.replace("_", " ")}</span>` : "";
    return `<div class="msg tool"><span class="tool-name">tool: ${m.tool}</span>${statusBadge}<div>${m.detail}</div></div>`;
  }
  return `<div class="msg ${m.role}">${m.text}</div>`;
}

function replayChatScript(script) {
  const msgs = document.getElementById("chat-messages");
  msgs.innerHTML = "";
  let i = 0;
  const step = () => {
    if (i >= script.length) return;
    msgs.insertAdjacentHTML("beforeend", renderChatMsg(script[i]));
    msgs.scrollTop = msgs.scrollHeight;
    i += 1;
    setTimeout(step, 550);
  };
  step();
}
