---
name: sdlc-reviewer
description: "Reviews a SnowComotive dbt model PR against its story's acceptance criteria and the LLD invariants it must preserve, and runs dbt run/dbt test against it. Use after a Developer-skill PR is open, before merging. Triggers: review this PR, check SH-<n>'s PR, run dbt test on this change, is this ready to merge."
---

# SDLC: Reviewer

Scoped to **dbt model development only** — same boundary as `sdlc-design`/`sdlc-developer`. Never merges — that's always a human action.

## Workflow

1. **Identify the changed model(s)** in the PR (via `git diff main...<branch>` or the PR's file list).
2. **Run tests**: `dbt run --select <model>+` and `dbt test` — against the changed model and its downstream dependents (the `+` matters; a change with no visible downstream impact might still break something a few models later).
3. **Check against acceptance criteria**: re-read the story's Jira description and the Design skill's note (if one exists) — does the implementation actually match what was designed, not just "does it run"?
4. **Check named invariants explicitly** — don't just run generic tests. If the story touches the sensor path or `FEAST` models, specifically verify:
   - **Leakage-safety** (FR-PL-03): any `hours_since_last_service`-style join uses strictly-less-than / ASOF semantics, never a same-or-later event.
   - **Backward-only rolling windows** (FR-FS-09): `ROWS BETWEEN N PRECEDING AND CURRENT ROW`, never a window that looks forward — this is what the whole 15-minute incremental-refresh design depends on.
   - **Materialization correctness**: `dynamic_table` + `target_lag` used where the design note calls for it, not silently downgraded to `table`/`view`.
5. **Report pass/fail with specifics** — not just "looks good." If something regresses a named invariant, say which invariant explicitly, don't just say "found an issue."
6. **Leave PR comments** (`gh pr comment` / `gh pr review --comment`) summarizing the findings.

## Stopping point

**Always stop after reporting.** Never merge the PR, and never tell the user it's "safe to merge" as an instruction — report findings, let the user decide (FR-SDLC-02's human-merge gate, also enforced by `board-triage`'s guardrails).

## Output

Pass/fail summary + inline PR comments. No merge, no file changes — if something needs fixing, hand back to `sdlc-developer`, don't fix it yourself under this skill.
