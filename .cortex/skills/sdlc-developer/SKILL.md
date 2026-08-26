---
name: sdlc-developer
description: "Writes the actual dbt model SQL/macro/schema.yml files for a SnowComotive Jira story, following an already-approved design note. Use after a design note exists and has been approved for a dbt Raw/Standardized/Consumption/FEAST story. Triggers: implement this story, write the dbt model, build SH-<n>, develop this design."
---

# SDLC: Developer

Scoped to **dbt model development only** — same boundary as `sdlc-design`. Requires an approved design note (from `sdlc-design`) as input; don't skip straight here without one unless the user explicitly says the story is trivial enough not to need it.

## Workflow

1. **Read the approved design note** (Jira comment or chat context) — this defines the SQL shape, materialization, and affected tables. Don't re-derive these from scratch.
2. **Write the dbt model file(s)**:
   - Correct schema prefix and directory (`models/raw/`, `models/std/`, `models/cons/`, `models/feast/`) matching `RAW`/`STD`/`CONS`/`FEAST`.
   - Materialization exactly as the design note specifies — sensor-path models are `dynamic_table` with `target_lag = '15 minutes'` (LLD Module 1/3), FEAST inference models likewise, FEAST training snapshots are plain `table`. Don't default to `view`/`table` if the note says `dynamic_table`.
   - If the story touches `FEAST.sensor_rolling_features`, edit the existing macro rather than duplicating its logic into a new one — one shared definition (FR-FS-01).
3. **Write/update the `schema.yml` entry**: layer tag (`raw`/`standardized`/`consumption`/`feast`), and **at least one test** (not_null/relationships/accepted_values) on key columns per FR-PL-08 — not optional, every model needs it.
4. **Follow the project's git workflow** (see `board-triage`'s Step 4a/4b — same convention, don't re-derive it here): work happens on the story's already-created feature branch. Commit the new/changed `.sql`+`.yml` files with a message referencing the Jira key.
5. **Don't open the PR yourself if `board-triage` is already driving this story** — check whether `board-triage`'s Step 4b already owns the PR/Review-transition for this session. If this skill was invoked standalone (no `board-triage` session active), open the PR here instead: `gh pr create`, title `[SH-<n>] <summary>`, then tell the user to move the story to Review.

## Stopping point

None required mid-workflow — the PR itself is the checkpoint, reviewed next by `sdlc-reviewer`.

## Output

New/changed `.sql` + `.yml` files, committed to the story's branch; a PR open against `main` (unless `board-triage` already owns that step for this session).
