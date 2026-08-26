---
name: board-triage
description: "Prioritize and work the SnowComotive Jira Kanban board (project SH). Use whenever the user asks what to work on next, wants to prioritize/filter stories, says the board is empty, wants to pull stories onto the board, move a story to in-progress, or start work on the next item in the backlog. Triggers: what should we work on next, prioritize the board, pull next story, board is empty, move to in progress, what's our current priority, pick next story, start next story."
---

# Board Triage (SnowComotive, Jira project SH)

Front door to the backlog. Decides what to work on next and hands off — it does NOT do the actual design/dev/review/doc work itself. That's still the 4 SDLC skills (Design/Developer/Reviewer/Documenter, once built per EPIC-SDLC) or the relevant bundled skill (`snowpark-python`, `developing-with-streamlit-in-snowflake`, `agent-studio`, etc.) depending on the story's label.

Traces to: `docs/05-Epics.md` (backlog source of truth), Jira project **SH** (cloudId `5668f0d3-53d5-48a7-b9b5-163c7d4c0574`, site `chirajpepz.atlassian.net`).

## Priority model (fixed, do not re-derive)

Epics are tiered by label: `P0` (walking skeleton, do first) → `P1` (target scope) → `P2` (stretch/bonus) → `P3` (teardown, deliberately last). Within a tier, Epic order follows `docs/05-Epics.md`'s section order. Stories are tagged with the same tier label as their parent Epic, plus a dev-tool label (`sdlc-skill`, `snowpark`, `streamlit`, `agent`, `ops`, `jira`, `machine-learning`) that determines what to hand off to at Step 4.

## Workflow

### Step 1: Check the board

Query current state with the Atlassian MCP tools (`mcp_atlassian_searchJiraIssuesUsingJql`):

```
project = SH AND status != Done ORDER BY status
```

Group results by status column. Project SH's actual workflow (confirmed 2026-08-26, re-verify with `mcp_atlassian_getTransitionsForJiraIssue` if it looks stale) is **To Do → In Progress → Review → Done** — a `Review` status was added specifically to gate on PR review/merge (see Step 4a/4b below). There is no separate Docs column; documentation-only stories (e.g. Documenter-skill work) still go through the same 4 statuses.

### Step 2: Determine current-priority Epic

**If the board (non-Backlog columns) already has active stories** — the current-priority Epic is whichever Epic those active stories belong to. Continue working that Epic; do not jump to a different tier mid-stream without asking.

**If the board is empty** (everything sitting in Backlog/To Do untouched):
1. Query Epics: `project = SH AND issuetype = Epic ORDER BY labels`
2. Pick the lowest-tier Epic (`P0` first) that still has at least one Story not in Done.
3. That is the current-priority Epic.

### Step 3: Discuss and select — ALWAYS stop here, never auto-pull

Query that Epic's Stories: `project = SH AND parent = "<EPIC-KEY>" AND status = "To Do" ORDER BY key`

(This project's workflow only has 3 statuses — **To Do / In Progress / Done** — there is no `Backlog` status. "Backlog" here is a separate Kanban-board concept: whether an issue has been placed onto the active board view at all, independent of its status. See the tool-gap note in Step 4 below — this skill cannot query or change that board-membership state via the API, only via `status`.)

Present the candidate list to the user (key, summary, labels) and discuss:
- Which stories make sense to pull onto the board this round (usually the next 1-3 in doc order, but the user may reorder — e.g. pulling a spike story like S-RUL-0 ahead of its siblings, or skipping a story that's blocked).
- What order to work them in.

**Do not transition any issue until the user confirms the selection.** This is a deliberate design choice (human-gated governance, matches FR-SDLC-02) — never auto-pull without asking, even if it would be faster.

Use `ask_user_question` if the choice isn't obvious from the conversation; a plain-text confirmation ("yes, pull S-ENV-1 and S-ENV-2 next") is also acceptable to proceed on.

### Step 4: Move selected stories, then hand off

**Known tool-gap (confirmed 2026-08-26): moving an issue from Backlog onto the active Kanban Board is NOT possible via the available Atlassian MCP tools.** They wrap Jira's core REST API v3 (issues, search, transitions, comments, links) but not the Agile REST API's backlog/board endpoints — there is no `mcp_atlassian_*` tool for board membership. Do not attempt to work around this by guessing at a hidden field via `editJiraIssue`; it will not work and risks corrupting issue data.

**What this means in practice**: after Step 3's confirmation, tell the user exactly which issues to manually drag from Backlog onto the Board in the Jira UI (or use the board's "Move to Board" bulk action). Status transitions (below) work fine via API regardless of whether an issue has been placed on the board yet — this skill's transition calls are not blocked by board membership, so you can transition an issue to In Progress even before/without confirming it's visually on the board. If the user says they've already moved something to the board manually (as with SH-10/SH-14), just proceed with the transition.

For each confirmed story:
1. Transition it to **To Do** if not already there (`mcp_atlassian_transitionJiraIssue`) — always call `mcp_atlassian_getTransitionsForJiraIssue` first to get the current transition ID; do not hardcode IDs, they shift as the workflow evolves (a `Review` status was added mid-project — see Step 4a).
2. When the user says to actually start one (not just queue it): **create the git branch first (Step 4a), then** transition the issue to **In Progress**.
3. **Hand off based on the story's dev-tool label**:
   - `sdlc-skill` label **and** the 4 SDLC skills already exist in this repo (check `.cortex/skills/` or wherever they were authored per EPIC-SDLC) → hand off to the **Design** skill first, following the Design → Dev → Review → Docs chain from there. Read the story's Jira description for its FR-ID/LLD-section reference before handing off — that's the Design skill's required input.
   - `sdlc-skill` label but the 4 skills **don't exist yet** → this story likely *is* one of the EPIC-SDLC stories (SH-12, SH-15–SH-18) or comes before them; just build it directly, no hand-off loop yet.
   - `snowpark` → work directly using the `snowpark-python` bundled skill.
   - `streamlit` → `developing-with-streamlit-in-snowflake`.
   - `agent` → `agent-studio`.
   - `jira` → direct implementation (stored procedures, Secret/External Access Integration per LLD Module 9); no bundled skill maps 1:1, use `sql-author`/`integrations` as needed.
   - `ops` → `snowflake-tasks`/`warehouse`/`sql-author` as appropriate (environment/lifecycle scripts).
   - `machine-learning` → `machine-learning` bundled skill.

### Step 4a: Create the git branch (before any implementation work starts)

**Every story or batch of stories worked In Progress gets its own branch off `main` — no implementation work happens directly on `main`.** This applies to ALL stories regardless of dev-tool label (not just `sdlc-skill`-labeled ones — the Developer skill's own branch/PR habit from LLD Module 11 §2 is now the project-wide norm, not a dbt-only pattern).

**Branch naming**: `<type>/SH-<epicNum>-<storyNum1>[-<storyNum2>...]-<slug>`
- `<type>` is `feature/` for new capability (the default), or `bugfix/` for fixing something broken/discovered.
- `<epicNum>` is the parent Epic's numeric ID (e.g. `2` for SH-2), `<storyNum>` is each story's numeric ID, hyphen-separated for a batch.
- `<slug>` is a short kebab-case description from the story summary.
- Example, single story SH-16 under Epic SH-5: `feature/SH-5-16-author-design-skill`
- Example, batch of SH-10+SH-14 under Epic SH-2: `feature/SH-2-10-14-env-setup`

Create it with `git checkout -b <branch-name> main` (or `git switch -c ... main`) via Bash before writing any code/SQL/config for that story. If the user already created uncommitted work on `main` before this rule existed (as happened with SH-10/SH-14), don't retroactively branch it — that's a one-time exception, not a pattern to repeat.

### Step 4b: Raise the PR, move to Review (not Done)

When the story's work is complete:
1. Commit the changes on the branch (only files relevant to this story — don't sweep in unrelated uncommitted changes without asking).
2. Push the branch and open a PR to `main` via `gh pr create`, title referencing the Jira key(s) (e.g. `[SH-16] Author Design skill`), body summarizing what changed and linking back to the story's FR-ID/LLD traceability.
3. Transition the Jira issue to **Review** (not Done) — get the transition ID via `mcp_atlassian_getTransitionsForJiraIssue` first.
4. Tell the user the PR is up and ask them to review/merge — do not merge it yourself unless explicitly told to.

### Step 5: Close the loop — only after the PR is merged

Once the user confirms the PR is merged (or you confirm via `gh pr view --json state` if asked to check): transition the issue from **Review** to **Done** and return to Step 1. Do not skip Review and jump straight to Done, even if the PR looks trivial — the human-merge gate is the point (FR-SDLC-02).

If that was the last open Story in the current-priority Epic, re-run Step 2 to pick the next Epic — tell the user which Epic that is before continuing, don't silently jump tiers.

## Adding new backlog items (Epic / Story / Task / Subtask)

New work surfaces mid-flight — a discovered bug, an unplanned follow-up, a spike that turned into more than expected. `docs/05-Epics.md` is the backlog **source of truth** (stated at the top of this skill) — Jira is a mirror of it, not the other way around. Every new item created in Jira must also land in the doc, or the two will drift and the doc stops being trustworthy. Never create in Jira only.

**Decide the size first, then act:**

1. **Subtask-sized** (a small piece of work fully inside an existing Story's scope — e.g. "also add a not_null test on this column"):
   - Create a Jira **Subtask** (`issueTypeName: Subtask`) with `parent` = the current Story's key.
   - Doc update optional — only add a line to the Story's Task/Sub-task list in `docs/05-Epics.md` if it's non-obvious enough that a future reader would want to know it happened.

2. **Story-sized** (a distinct deliverable with its own Definition of Done, but fits inside an existing Epic's scope):
   - Confirm with the user which existing Epic it belongs to (don't guess if ambiguous — ask).
   - Create a Jira **Story** with `parent` = that Epic's key, labeled with the Epic's tier (`P0`–`P3`) + the appropriate dev-tool tag.
   - **Append a row to that Epic's table in `docs/05-Epics.md`** (same format as existing rows: `S-<PREFIX>-<N>`, Task/Sub-task breakdown, FR-ID/LLD traceability if applicable) — this keeps the doc as the reproducible plan, not just Jira's live state.

3. **Epic-sized** (a body of work not covered by any of the current 9 Epics — a whole new tier of scope):
   - **Do not create unilaterally.** Propose it to the user first: name, priority tier, one-line summary, why it doesn't fit an existing Epic. Wait for confirmation.
   - On confirmation: add a new `## N. EPIC-<NAME>` section to `docs/05-Epics.md` (update the Epic summary table too), then create the Jira **Epic**, then its Stories per rule 2 above.
   - Record the new Epic's Jira key somewhere durable (e.g. update the Epic key map in project memory) so future triage sessions can reference it — don't make the user re-explain which key maps to which Epic every time.

**Mechanics reminder** (team-managed/next-gen Jira project SH): Epic↔Story and Story↔Subtask links both use the plain `parent` field — there's no separate Epic Link field. Subtasks parent to a Story/Task, never directly to an Epic.

## Guardrails

- Never move a story tier out of order (don't start P1 work while P0 stories sit untouched in Backlog) unless the user explicitly overrides — ask if it looks like that's about to happen.
- Never invent stories or re-derive priority from scratch — `docs/05-Epics.md` is the source of truth; if a story described there doesn't exist yet in Jira, say so rather than guessing its content.
- If a story's parent Epic can't be determined (e.g. Jira `parent` lookup fails), stop and ask rather than guessing which Epic it belongs to.
- **Never implement a story's work directly on `main`.** Create the branch (Step 4a) before writing anything. If you notice mid-task that work already started on `main` without a branch, stop and ask the user how to proceed rather than silently continuing or silently branching after the fact.
- Never merge a PR yourself unless the user explicitly says to — merging is the human gate (FR-SDLC-02), not something this skill decides on its own.

## Output

At each invocation: a short status ("board is empty, current priority is EPIC-X, here are its next 3 backlog stories") followed by either a discussion prompt (Step 3) or a confirmation of what was moved/handed off (Step 4/5).
