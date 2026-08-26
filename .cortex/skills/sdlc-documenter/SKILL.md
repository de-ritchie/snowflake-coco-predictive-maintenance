---
name: sdlc-documenter
description: "Updates dbt-level documentation (schema.yml descriptions, changelog) after a SnowComotive story's PR has been merged. Use after a PR is merged, before or as the story moves to Done. Triggers: document this merge, update the changelog, write descriptions for SH-<n>, documenter skill."
---

# SDLC: Documenter

Scoped to **dbt model development only** — same boundary as the other 3 SDLC skills. Does **not** rewrite the BRD/FRD/HLD/LLD documents — those are the project's design record and stay as-is; this skill keeps the *build artifact's* docs in sync with what actually got merged (which can drift from the original design as implementation details get resolved during review).

## Workflow

1. **Read the merged PR's diff** — which models/macros changed.
2. **Update `schema.yml` descriptions** for any new or changed model/column — clear enough to feed `dbt docs generate` meaningfully, not just restating the column name.
3. **Append a changelog entry** (a running build changelog file — create `CHANGELOG.md` at the dbt project root if it doesn't exist yet) with: date, Jira key(s), one-line summary of what changed and why.
4. **If the implementation diverged from the original design note** (e.g. a materialization got changed during review, a column got renamed) — note that divergence in the changelog entry. This is exactly the kind of drift this skill exists to catch; don't silently document the new state as if it was always the plan.
5. **Follow the same git workflow as every other story** (`board-triage`'s Step 4a/4b) — this is small enough to often be the tail end of the same branch/PR the Developer/Reviewer skills already used for this story, not a reason to work directly on `main`.

## Stopping point

None required — low-risk, additive documentation update (FR-SDLC per LLD Module 11 §4). The user can always amend afterward.

## Output

Updated `schema.yml` descriptions + a new/updated `CHANGELOG.md` entry.
