# Project Instructions

## Overview

SnowComotive — Predictive Maintenance & OEE Command Center. A hackathon submission demonstrating Cortex Code (CoCo) across the full planning→development→execution→testing lifecycle. Design docs: `docs/01-BRD.md` through `docs/04-*-LLD.md` (11 LLD modules), backlog: `docs/05-Epics.md`. Jira project **SH** (`chirajpepz.atlassian.net`) mirrors the backlog doc — `docs/05-Epics.md` is the source of truth, Jira is the working view.

## Architecture: skills vs. agents (read this before touching either folder)

**Skills** (`.cortex/skills/*/SKILL.md`) are markdown instructions injected into the *current* conversation — no isolated context, no separate dispatch. Reserved for purely mechanical, repeatable procedures with no real judgment call:
- `board-triage` — backlog CRUD/prioritization, decides *what* to work on next, dispatches the right agent.
- `dev-workflow` — git branch/PR/Jira-transition mechanics every agent below follows.

**Agents** (`.snowflake/cortex/agents/*.md`) are real subagents — own isolated context, dispatched via the `task` tool, `model: claude-sonnet-5`. Reserved for actual reasoning/judgment work:
- `Design-agent` — brainstorms a dbt-model story with the user, freezes a design doc to `docs/designs/SH-<key>-<slug>.md` once explicitly confirmed. No code.
- `Developer-agent` — implements strictly against the frozen design doc. No PR, no status change.
- `Reviewer-agent` — runs `dbt run`/`dbt test`, checks doc adherence + whatever named correctness invariants this project's FRD/LLD actually calls out (e.g. join-timing/leakage rules, incremental-window direction rules). Reports only, never merges.
- `Documenter-agent` — reconciles the design doc with what was built, updates `schema.yml`/`CHANGELOG.md`, attaches the final doc, opens the PR, moves the story to Review.
- `Triage-agent` — general executor for everything that isn't a dbt-model story (ops/snowpark/streamlit/agent/jira/machine-learning labels). Does branch→implement→PR→Review itself in one pass. **Not the same thing as the `board-triage` skill** — that only discusses priority, this one implements.
- `Genesis-agent` — bootstraps a *brand-new* project: brainstorms a problem statement with the user through cascading freeze checkpoints, writes the BRD→FRD→HLD→LLD doc stack from scratch, then (optionally) scaffolds genericized copies of the other 4 agents + `Triage-agent` + `board-triage`/`dev-workflow` into the new project. This is what makes the whole toolkit portable beyond this one repo — everything else here assumes `docs/*.md` already exists; this is what creates it.

**Critical constraint**: subagents cannot spawn other subagents. There is no auto-orchestration — sequencing through Design→Developer→Reviewer→Documenter (or through Genesis-agent's own 5 stages) is driven manually by the user, one agent at a time.

**Table-agnostic by design**: none of the 5 SDLC agent prompts hardcode a specific table/column/schema/FR-ID — that was a real bug fixed on 2026-08-26 (they used to reference this project's specific FR-PL-03/FR-FS-09/FEAST-macro naming despite claiming otherwise). Specifics always come from `docs/03-HLD.md` / the relevant `docs/04-*-LLD.md` module for the story at hand, or the frozen `docs/designs/` doc once one exists. If a prompt needs updating because the docs changed, update the prompt's *pointer* to the docs, not embedded specifics — this is what lets `Genesis-agent` copy these files verbatim into a different project without editing them.

**Known limitation**: `board-triage`/`dev-workflow` are NOT yet parameterized — they hardcode this repo's Jira cloudId/project key/label taxonomy directly in prose. `Genesis-agent`'s scaffold step copies them with placeholders for a new project's Jira details, but that's a manual fill-in, not a clean config file. Flagged as a possible future improvement, not built.

## Guidelines

- Never implement a story's work directly on `main` — every story gets its own branch (`dev-workflow` skill's naming convention: `<type>/SH-<epicNum>-<storyNum(s)>-<slug>`).
- Never merge a PR without the user's explicit go-ahead — that's the human gate this whole workflow is built around (FR-SDLC-02).
- `docs/05-Epics.md` is the backlog source of truth; if Jira and the doc ever disagree, the doc wins — fix Jira to match, or update the doc explicitly and say so, never let them silently drift.
- Two Jira "projects" exist conceptually — the SH Kanban board (this repo's own dev tracking, A10) and a future Jira Service Management project (A5, touched only by the *deployed* agents at runtime for live maintenance tickets). Don't conflate them.
