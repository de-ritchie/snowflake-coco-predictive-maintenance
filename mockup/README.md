# SnowComotive Streamlit Mockup

Standalone, static HTML/CSS/JS mockup of the Command Center app (Module 8 LLD) —
built while the underlying feature tables (FEAST, Module 4) are still in
progress. **No live Snowflake connection, no real agent/semantic-view calls.**
All data in `data.js` is hand-authored to match the demo narrative in
`docs/01-BRD.md` §7/§8, not queried from anywhere.

Open `index.html` directly in a browser — no server, no build step, no
external CDN dependencies (fully offline/self-contained).

## Files

| File | Purpose |
|---|---|
| `index.html` | App shell + all 5 pages (hidden/shown via JS, single-page-app style) |
| `styles.css` | Snowflake-branded theme (`#29B5E8` accent), responsive layout |
| `data.js` | Mock dataset: machines, sensor series, OEE, priority scores, forecast, personas/tools, scripted chat transcripts |
| `app.js` | Render + interaction logic: nav/persona switching, tick-injection animation, cross-page nav, hand-rolled inline-SVG line charts (no chart library) |

## Section → source traceability

| Mockup section | Design source | Jira story |
|---|---|---|
| Sidebar: persona switcher | `docs/04-8-LLD.md` §0, FR-CC-04 | SH-59 (S-PERSONA-3, To Do) |
| Sidebar: machine filter | `docs/04-8-LLD.md` §0 | — |
| Sidebar: "Inject next tick" | `docs/04-8-LLD.md` "Demo-only control", FR-CC-06 | SH-33 (S-OPS-POST-1b, To Do) |
| Overview page | `docs/04-8-LLD.md` §1, FR-CC-01 | SH-21 (S-APP-1, In Progress) |
| Priority Queue page | `docs/04-8-LLD.md` §2, FR-CC-02; formula in `docs/03-HLD.md` §5 | SH-48 (S-RUL-7, To Do) |
| Forecast OEE page | `docs/04-8-LLD.md` §3, FR-CC-03 | SH-48 (S-RUL-7, To Do) |
| Agent Chat page | `docs/04-8-LLD.md` §4, FR-CC-05; tool specs in `docs/04-7-LLD.md` | SH-32 (S-APP-2, In Progress), SH-52 (S-JIRA-4, To Do) |
| Impact Statement page | `docs/04-8-LLD.md` §5, FR-CC-07, BRD §8 | SH-57 (S-IMPACT-1, To Do) |

## Confirmed UX decisions (from the mockup design discussion)

- **Agent Chat is a dedicated tab**, matching Module 8 §4 exactly — not a
  persistent docked side panel. This was a deliberate choice among options
  considered (docked panel, toggle-between-both) — revisit if real usage
  testing suggests otherwise.
- **Theme**: Snowflake-branded (`#29B5E8` accent, light background,
  Snowsight-style cards/tables) rather than dark-industrial or plain-neutral.
- **"Inject next tick" animates mock data** (health scores, OEE/forecast
  charts, priority ranking) rather than just showing a static toast — sells
  the "live tick propagates to a chart in the demo session" requirement
  (`docs/05-Epics.md` §Definition of Done for the walking skeleton).

## Demo narrative baked into the mock data

Per `docs/01-BRD.md` assumption 15 / §7 beat 3: **CNC Boring** (Caliper line)
ranks #1 in Priority Queue not because it has the worst anomaly score or RUL —
**CNC Horizontal Machining Center** (Engine Head line) does — but because
Caliper-line demand pressure dominates the priority formula right now. The
Forecast OEE page's two shaded risk windows show the "Caliper dip next week"
and "Engine Head's own overdue PM catching up in weeks 5-8" beats side by
side. Clicking "Inject next tick" jitters these numbers but doesn't rewrite
the underlying story.

## Known gaps vs. the real app (deferred, per Module 8 LLD "Deferred to build time")

- Exact chart library / widget code — this mockup hand-rolls inline SVG line
  charts instead of `st.line_chart`/Plotly, purely for a dependency-free
  static file.
- Anomaly-score-to-badge thresholds are placeholder cutoffs, not tuned against
  real model output.
- Tool-call confirmation UI here is a static styled chip
  (`CREATED`/`ALREADY_OPEN`/`RECENTLY_CLOSED`) — whether the real app needs a
  custom Streamlit component or can use the agent's native structured
  tool-result rendering is still open (Module 8 LLD, Deferred section).
- Chat input box is disabled — transcripts are scripted/replayable
  (▶ button), not a live model.
