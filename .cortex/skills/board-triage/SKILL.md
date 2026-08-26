---
name: board-triage
description: "Prioritize and work the SnowComotive Jira Kanban board (project SH). Use whenever the user asks what to work on next, wants to prioritize/filter stories, says the board is empty, wants to pull stories onto the board, move a story to in-progress, or start work on the next item in the backlog. Triggers: what should we work on next, prioritize the board, pull next story, board is empty, move to in progress, what's our current priority, pick next story, start next story."
---

# Board Triage (SnowComotive, Jira project SH)

Front door to the backlog. Decides what to work on next and **dispatches the right agent via the `task` tool** — it does NOT do the actual design/dev/review/doc work itself, and it is not itself an agent (it's the skill that decides *what*, then hands off to the agent that decides *how*). The 4 SDLC agents (`Design-agent`/`Developer-agent`/`Reviewer-agent`/`Documenter-agent`, LLD Module 11) handle `sdlc-skill`-labeled dbt-model stories; `Triage-agent` handles everything else. See the `dev-workflow` skill for the git/PR/Jira mechanics those agents use — this skill doesn't own that anymore, only the priority/selection decision.

Traces to: `docs/05-Epics.md` (backlog source of truth), Jira project **SH** (cloudId `5668f0d3-53d5-48a7-b9b5-163c7d4c0574`, site `chirajpepz.atlassian.net`).

## Priority model (fixed, do not re-derive)

Epics are tiered by label: `P0` (walking skeleton, do first) → `P1` (target scope) → `P2` (stretch/bonus) → `P3` (teardown, deliberately last). Within a tier, Epic order follows `docs/05-Epics.md`'s section order. Stories are tagged with the same tier label as their parent Epic, plus a dev-tool label (`sdlc-skill`, `snowpark`, `streamlit`, `agent`, `ops`, `jira`, `machine-learning`) — `sdlc-skill` means "route through the Design→Developer→Reviewer→Documenter agent chain," any other label means "dispatch `Triage-agent`" (Step 4).

## Workflow

### Step 1: Check the board

Query current state with the Atlassian MCP tools (`mcp_atlassian_searchJiraIssuesUsingJql`):

```
project = SH AND status != Done ORDER BY status
```

Group results by status column. Project SH's actual workflow (confirmed 2026-08-26, re-verify with `mcp_atlassian_getTransitionsForJiraIssue` if it looks stale) is **To Do → In Progress → Review → Done** — a `Review` status was added specifically to gate on PR review/merge, set by whichever agent is dispatched at Step 4 (via the `dev-workflow` skill), not this skill directly. There is no separate Docs column; documentation-only work (e.g. `Documenter-agent`'s output) still goes through the same 4 statuses.

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

### Step 4: Move to In Progress, then dispatch the right agent

**Known tool-gap (confirmed 2026-08-26): moving an issue from Backlog onto the active Kanban Board is NOT possible via the available Atlassian MCP tools.** They wrap Jira's core REST API v3 (issues, search, transitions, comments, links) but not the Agile REST API's backlog/board endpoints — there is no `mcp_atlassian_*` tool for board membership. Do not attempt to work around this by guessing at a hidden field via `editJiraIssue`; it will not work and risks corrupting issue data.

**What this means in practice**: after Step 3's confirmation, tell the user exactly which issues to manually drag from Backlog onto the Board in the Jira UI (or use the board's "Move to Board" bulk action). Status transitions (below) work fine via API regardless of whether an issue has been placed on the board yet — this skill's transition calls are not blocked by board membership, so you can transition an issue to In Progress even before/without confirming it's visually on the board. If the user says they've already moved something to the board manually (as with SH-10/SH-14), just proceed with the transition.

For each confirmed story:
1. Transition it to **In Progress** (`mcp_atlassian_transitionJiraIssue`) — always call `mcp_atlassian_getTransitionsForJiraIssue` first to get the current transition ID; do not hardcode IDs, they shift as the workflow evolves.
2. **Dispatch the right agent via the `task` tool** — this skill's job ends here; it does not do the git/PR/Jira mechanics itself anymore (that's the `dev-workflow` skill, used by whichever agent is dispatched):
   - `sdlc-skill` label → dispatch **`Design-agent`** (`task` tool, `subagent_type: "Design-agent"`). Pass it the story key and its FR-ID/LLD-section reference. Design-agent brainstorms with the user and freezes a design doc — it does not chain into Developer/Reviewer/Documenter automatically (agents can't spawn other agents, and the user drives sequencing manually per story). Tell the user explicitly that once the design is frozen, they'll need to invoke `Developer-agent`, then `Reviewer-agent`, then `Documenter-agent` themselves when ready.
   - Any other label (`snowpark`/`streamlit`/`agent`/`jira`/`ops`/`machine-learning`) → dispatch **`Triage-agent`** (`task` tool, `subagent_type: "Triage-agent"`). It handles the full branch→implement→PR→Review cycle itself in one pass.
3. If a story is one of EPIC-SDLC's own authoring stories (building the agents/skills themselves) — those are infrastructure-for-the-process, not process-following-the-process; dispatch `Triage-agent` for them too (as was done for SH-16/12/17/19), not `Design-agent` on itself.

### Step 5: Close the loop — only after the PR is merged

Once the user confirms the PR is merged (or you confirm via `gh pr view --json state` if asked to check): transition the issue from **Review** to **Done** and return to Step 1. Do not skip Review and jump straight to Done, even if the PR looks trivial — the human-merge gate is the point (FR-SDLC-02). The Review transition itself already happened inside the dispatched agent's own workflow (`Documenter-agent` or `Triage-agent`, via `dev-workflow`) — this skill only handles the final Review→Done step, once you've confirmed the merge.

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
- Never merge a PR yourself unless the user explicitly says to — merging is the human gate (FR-SDLC-02), enforced by whichever agent handled the story, not this skill's own decision to make.
- Never do implementation work in this skill itself — if you find yourself about to write code/SQL/config directly instead of dispatching an agent, stop; that's a sign Step 4 was skipped.

## Output

At each invocation: a short status ("board is empty, current priority is EPIC-X, here are its next 3 backlog stories") followed by either a discussion prompt (Step 3) or confirmation of which agent was dispatched (Step 4) / which stories moved to Done (Step 5).
