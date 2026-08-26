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
- `Reviewer-agent` — runs `dbt run`/`dbt test`, checks doc adherence + named invariants (leakage-safety FR-PL-03, backward-only windows FR-FS-09). Reports only, never merges.
- `Documenter-agent` — reconciles the design doc with what was built, updates `schema.yml`/`CHANGELOG.md`, attaches the final doc, opens the PR, moves the story to Review.
- `Triage-agent` — general executor for everything that isn't a dbt-model story (ops/snowpark/streamlit/agent/jira/machine-learning labels). Does branch→implement→PR→Review itself in one pass. **Not the same thing as the `board-triage` skill** — that only discusses priority, this one implements.

**Critical constraint**: subagents cannot spawn other subagents. There is no auto-orchestration — sequencing through Design→Developer→Reviewer→Documenter is driven manually by the user, one agent at a time.

**Table-agnostic by design**: none of the 5 agent prompts hardcode a specific table/column/schema. That always comes from `docs/03-HLD.md` / the relevant `docs/04-*-LLD.md` module for the story at hand, or the frozen `docs/designs/` doc once one exists. If a prompt needs updating because the docs changed, update the prompt's *pointer* to the docs, not embedded specifics.

## Guidelines

- Never implement a story's work directly on `main` — every story gets its own branch (`dev-workflow` skill's naming convention: `<type>/SH-<epicNum>-<storyNum(s)>-<slug>`).
- Never merge a PR without the user's explicit go-ahead — that's the human gate this whole workflow is built around (FR-SDLC-02).
- `docs/05-Epics.md` is the backlog source of truth; if Jira and the doc ever disagree, the doc wins — fix Jira to match, or update the doc explicitly and say so, never let them silently drift.
- Two Jira "projects" exist conceptually — the SH Kanban board (this repo's own dev tracking, A10) and a future Jira Service Management project (A5, touched only by the *deployed* agents at runtime for live maintenance tickets). Don't conflate them.
