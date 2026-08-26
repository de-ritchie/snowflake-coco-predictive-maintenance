---
name: sdlc-design
description: "Turns a SnowComotive Jira story about a dbt model change into a short design note before any code is written. Use when starting work on any 'sdlc-skill'-labeled Jira story (dbt Raw/Standardized/Consumption/FEAST work only, not Snowpark/Streamlit/ops). Triggers: design this story, write a design note, what's the design for SH-<n>, plan this dbt model change."
---

# SDLC: Design

Scoped to **dbt model development only** (Raw/Standardized/Consumption/FEAST schemas — LLD Modules 1/3/4). Not for Snowpark scripts, Streamlit, agents, or ops/lifecycle scripts — those stories use their own bundled CoCo skill directly (see `board-triage`'s hand-off table); no design note needed for them.

## Workflow

1. **Read the Jira story** (Atlassian MCP tools) — get its FR-ID/LLD-section reference from the description; SnowComotive stories always cite one.
2. **Load the relevant LLD module(s)** cited (Module 1 = table DDL, Module 3 = Standardization/Consumption build, Module 4 = FEAST feature engineering) and skim the existing dbt project structure (`models/`, `macros/`) for the actual current state — the LLD is the design intent, the repo is the ground truth of what's already built.
3. **Write a design note** covering:
   - Which Consumption/FEAST table(s) this story affects (new or changed).
   - The exact SQL shape: joins, CTEs, materialization (`table`/`view`/`dynamic_table` + `target_lag` if applicable), macro usage if it touches `FEAST.sensor_rolling_features`.
   - Which FR-ID/LLD section this traces to (copy forward from the story, don't re-derive).
   - Any named correctness invariant this change must preserve (e.g. leakage-safety per FR-PL-03, backward-only rolling windows per FR-FS-09) — call these out explicitly if the story touches sensor-path or FEAST models, since the Reviewer skill checks for them later.
4. **If the story needs something the LLD doesn't already specify** — say so explicitly in the note and stop. Do not invent new architecture on the fly; that's a decision for the user, not this skill.
5. **Attach the design note** to the Jira story (as a comment, `mcp_atlassian_addCommentToJiraIssue`) and present it to the user in chat.

## Stopping point

**Always stop here.** The user reviews the design note before authorizing the Developer skill (`sdlc-developer`) to proceed — this is the human gate (FR-SDLC-01/02).

## Output

A markdown design note (in chat + as a Jira comment) — no code, no files written yet.
