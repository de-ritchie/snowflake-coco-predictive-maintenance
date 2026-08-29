---
name: board-triage
description: "Prioritize and work the SnowComotive Jira Kanban board (project SH). Use whenever the user asks what to work on next, wants to prioritize/filter stories, says the board is empty, wants to pull stories onto the board, move a story to in-progress, or start work on the next item in the backlog. Triggers: what should we work on next, prioritize the board, pull next story, board is empty, move to in progress, what's our current priority, pick next story, start next story."
---

# Board Triage (SnowComotive, Jira project SH)

Front door to the backlog. Decides what to work on next, moves it to In Progress, and initiates `dev-workflow` (branch creation) — then **stops and hands control back to the user**. It does NOT dispatch Design/Developer/Reviewer/Documenter/Triage agents and does NOT do any design/dev/review/doc work itself. Its only dependent skill is `dev-workflow` (git/PR/Jira mechanics). The user decides when and which agent to invoke for actual implementation — that is a separate, explicit action outside this skill.

Traces to: `docs/05-Epics.md` (backlog source of truth), Jira project **SH** (cloudId `5668f0d3-53d5-48a7-b9b5-163c7d4c0574`, site `chirajpepz.atlassian.net`).

## Priority model (fixed, do not re-derive)

Two signals exist, and they are not the same granularity — do not conflate them:

1. **Jira `priority` field (Highest/High/Medium/Low/Lowest)** — the fine-grained, authoritative "what's actually next" signal. This is set per-issue in Jira and can (and does) differ from an issue's label tier — e.g. SH-18 carries label `P0` but has `priority = Lowest`, because it's polish/publishing work on already-merged agents, not build-blocking. **This field wins whenever it's populated.** Sort/select by it first.
2. **Label tiers (`P0`/`P1`/`P2`/`P3`) + doc-section order** — the coarse, structural signal from `docs/05-Epics.md`: Epics are tiered by label (`P0` walking skeleton → `P1` target scope → `P2` stretch/bonus → `P3` teardown), and within a tier Epic order follows the doc's section order. Use this **only as a tiebreak** among issues that carry the same Jira `priority` value, or as a fallback when `priority` is unset/uninformative (e.g. everything defaulted to Medium with nothing standing out).

Stories are also tagged with a dev-tool label (`sdlc-skill`, `snowpark`, `streamlit`, `agent`, `ops`, `jira`, `machine-learning`) — `sdlc-skill` means "route through the Design→Developer→Reviewer→Documenter agent chain," any other label means "dispatch `Triage-agent`" (Step 4). This routing label is orthogonal to priority — check it independently at Step 4.

## Workflow

### Step 1: Check the board

Query current state with the Atlassian MCP tools (`mcp_atlassian_searchJiraIssuesUsingJql`) — always sort server-side via JQL rather than pulling everything unsorted and sorting locally:

```
project = SH AND status != Done ORDER BY priority DESC, status
```

Request only the fields you need (`fields: ["summary", "status", "priority", "labels", "parent", "issuetype"]`) to keep the payload manageable — Jira's API nests the full parent-Epic object inside every Story regardless, so large boards will still produce a big response; see "Tooling notes" below for how to handle that without resorting to ad-hoc scripting. Group results by status column. Project SH's actual workflow (confirmed 2026-08-26, re-verify with `mcp_atlassian_getTransitionsForJiraIssue` if it looks stale) is **To Do → In Progress → Review → Done** — a `Review` status was added specifically to gate on PR review/merge, set by whichever agent is dispatched at Step 4 (via the `dev-workflow` skill), not this skill directly. There is no separate Docs column; documentation-only work (e.g. `Documenter-agent`'s output) still goes through the same 4 statuses.

### Step 2: Determine current-priority Epic

**If the board (non-Backlog columns) already has active stories** — the current-priority Epic is whichever Epic those active stories belong to. Continue working that Epic; do not jump to a different tier mid-stream without asking.

**If the board is empty** (everything sitting in Backlog/To Do untouched):
1. Query all open Stories directly, sorted by the real signal: `project = SH AND issuetype = Story AND status = "To Do" ORDER BY priority DESC, key`. Look at the `priority` field on the top results first — Jira's own field, not the label tier.
2. If one or more Stories carry a distinctly higher `priority` (e.g. Highest, when the rest are Medium/Lowest) — those are the current-priority candidates, **regardless of which Epic they belong to or what label tier they carry**. Their shared parent Epic (if they share one) is the current-priority Epic.
3. If `priority` is unset or uniformly uninformative across all open Stories (e.g. everything is Medium with nothing standing out) — fall back to the label-tier heuristic: query Epics (`project = SH AND issuetype = Epic ORDER BY labels`), pick the lowest-tier Epic (`P0` first, doc-section order as tiebreak) that still has at least one Story not Done.

### Step 3: Discuss and select — ALWAYS stop here, never auto-pull

Query that Epic's Stories, still sorted by priority: `project = SH AND parent = "<EPIC-KEY>" AND status = "To Do" ORDER BY priority DESC, key`

(This project's workflow only has 3 statuses — **To Do / In Progress / Done** — there is no `Backlog` status. "Backlog" here is a separate Kanban-board concept: whether an issue has been placed onto the active board view at all, independent of its status. This is a **known tool-gap** (see Step 4) — the Atlassian MCP tools cannot query or change that board-membership state, only `status`. If Jira `priority` is unset/uninformative and label-tier order is also ambiguous, say so honestly rather than claiming to have checked Backlog membership you cannot actually see via these tools.)

Present the candidate list to the user (key, summary, **priority**, labels) and discuss:
- Which stories make sense to pull onto the board this round — lead with `priority`-ranked order, not doc order, when priority is populated (usually the next 1-3 by `priority DESC` then key; the user may still reorder — e.g. pulling a spike story like S-RUL-0 ahead of its siblings, or skipping a story that's blocked).
- What order to work them in.

**Do not transition any issue until the user confirms the selection.** This is a deliberate design choice (human-gated governance, matches FR-SDLC-02) — never auto-pull without asking, even if it would be faster.

Use `ask_user_question` if the choice isn't obvious from the conversation; a plain-text confirmation ("yes, pull S-ENV-1 and S-ENV-2 next") is also acceptable to proceed on.

### Step 4: Move to In Progress, initiate dev-workflow, then stop

**Known tool-gap (confirmed 2026-08-26): moving an issue from Backlog onto the active Kanban Board is NOT possible via the available Atlassian MCP tools.** They wrap Jira's core REST API v3 (issues, search, transitions, comments, links) but not the Agile REST API's backlog/board endpoints — there is no `mcp_atlassian_*` tool for board membership. Do not attempt to work around this by guessing at a hidden field via `editJiraIssue`; it will not work and risks corrupting issue data.

**What this means in practice**: after Step 3's confirmation, tell the user exactly which issues to manually drag from Backlog onto the Board in the Jira UI (or use the board's "Move to Board" bulk action). Status transitions (below) work fine via API regardless of whether an issue has been placed on the board yet — this skill's transition calls are not blocked by board membership, so you can transition an issue to In Progress even before/without confirming it's visually on the board. If the user says they've already moved something to the board manually (as with SH-10/SH-14), just proceed with the transition.

This skill's dependencies stop at **`board-triage`** (this file) and **`dev-workflow`** — it does not dispatch Design/Developer/Reviewer/Documenter/Triage agents itself. Deciding *what's next* and getting the story ready to work is this skill's whole job; actually implementing it is a separate, explicit step the user takes afterward.

For each confirmed story:
1. Transition it to **In Progress** (`mcp_atlassian_transitionJiraIssue`) — always call `mcp_atlassian_getTransitionsForJiraIssue` first to get the current transition ID; do not hardcode IDs, they shift as the workflow evolves.
2. **Initiate `dev-workflow`**: create (or reuse, if it already exists) the story's branch per `dev-workflow`'s §1 naming convention (`git checkout -b <type>/SH-<epicNum>-<storyNum>-<slug> main`). Do not implement anything, do not open a PR, do not dispatch any agent.
3. **Tell the user which agent they'd invoke when ready** — purely informational, not an action taken here: `sdlc-skill` label → `Design-agent` (starts the Design→Developer→Reviewer→Documenter chain); any other label (`snowpark`/`streamlit`/`agent`/`jira`/`ops`/`machine-learning`, including EPIC-SDLC's own authoring stories) → `Triage-agent`.
4. **Stop and hand control back to the user.** Do not proceed to implementation, PR, or further status transitions on your own — those happen only when the user explicitly invokes the relevant agent themselves.

### Step 5: Close the loop — only after the PR is merged

This step is invoked separately by the user once they've driven a story through implementation themselves (via whichever agent they chose at Step 4) and a PR is open and merged — it does not follow automatically from Step 4 in the same turn. Once the user confirms the PR is merged (or you confirm via `gh pr view --json state` if asked to check): transition the issue from **Review** to **Done** and return to Step 1. Do not skip Review and jump straight to Done, even if the PR looks trivial — the human-merge gate is the point (FR-SDLC-02).

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

## Tooling notes: MCP is the only Jira interface, filter/sort server-side

This skill talks to Jira exclusively through the `mcp_atlassian_*` tools — there is no separate raw-API path. Every query must do its filtering and sorting **in the JQL itself** (`AND`, `ORDER BY priority DESC`, etc.), not by pulling a broad result set and post-processing it locally:

- **Sort by priority, not by pulling-then-sorting**: use `ORDER BY priority DESC, key` (or `status`) directly in the JQL string passed to `mcp_atlassian_searchJiraIssuesUsingJql`. Never fetch unsorted results intending to re-sort them yourself afterward.
- **Filter by priority/label/status in JQL**, e.g. `project = SH AND priority = Highest AND status = "To Do"`, instead of fetching everything and filtering in a script.
- **Trim `fields`** to just what's needed (`summary`, `status`, `priority`, `labels`, `parent`, `issuetype`) — this is the only lever exposed for payload size; Jira's API will still nest a full parent-Epic sub-object inside every Story regardless, so large boards can still produce a big JSON response.
- **A local script (bash/python) to parse a tool's own JSON output is a documented last resort only** — justified solely when a single MCP response is too large for the Read tool's line-based view to usefully inspect (e.g. many issues with deep parent nesting), and even then it must only *read/group* data already returned by an MCP call, never substitute for one. If you find yourself reaching for a script to filter or sort, stop and move that filter/sort into the JQL instead — that's almost always possible and is the correct fix.

## Guardrails

- Never move a story tier out of order (don't start P1 work while P0 stories sit untouched in Backlog) unless the user explicitly overrides — ask if it looks like that's about to happen.
- Never invent stories or re-derive priority from scratch — `docs/05-Epics.md` is the source of truth; if a story described there doesn't exist yet in Jira, say so rather than guessing its content.
- If a story's parent Epic can't be determined (e.g. Jira `parent` lookup fails), stop and ask rather than guessing which Epic it belongs to.
- Never merge a PR yourself unless the user explicitly says to — merging is the human gate (FR-SDLC-02).
- Never dispatch an implementation agent (Design/Developer/Reviewer/Documenter/Triage) from within this skill — Step 4 ends at branch creation; invoking an agent is the user's explicit next action, not this skill's.
- Never do implementation work in this skill itself — if you find yourself about to write code/SQL/config directly, stop; that's a sign Step 4 was skipped and control wasn't actually handed back.

## Output

At each invocation: a short status ("board is empty, current priority is EPIC-X, here are its next 3 backlog stories") followed by either a discussion prompt (Step 3), or confirmation that the story is In Progress with its branch created and control handed back (Step 4), or which stories moved to Done (Step 5).
