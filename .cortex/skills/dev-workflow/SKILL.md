---
name: dev-workflow
description: "Purely mechanical git/Jira procedures: querying the Jira board (JQL), branch naming/creation, committing, opening a PR, attaching a comment to a Jira issue, and status transitions. Referenced exclusively by Jira-Triage-agent — the only agent that touches git/Jira, so it's the only agent that needs this skill. Triggers: query the board, create a branch for this story, commit this, open the PR, attach this doc to the Jira issue, move this story to review."
---

# Dev Workflow (mechanical procedures)

This skill has no judgment calls — it's the reusable "how" for every git/Jira mechanic in this project, so `Jira-Triage-agent`'s own prompt can stay focused on *when*/*whether* to act instead of re-deriving JQL/git incantations each time. It does not decide what to build, what to prioritize, or when to move to the next stage — that's `Jira-Triage-agent`'s (and the user's) call.

**Scope note**: `Design-agent`/`Developer-agent`/`Reviewer-agent`/`Documenter-agent` never touch Jira or mutate git state at all (no branch/commit/push/PR/comment/transition) — only `Jira-Triage-agent` does, and only `Jira-Triage-agent` needs this skill. If you're any other agent and find yourself reaching for a procedure below, stop — that's a sign the work belongs to `Jira-Triage-agent` instead.

Traces to: FR-SDLC-02, `docs/05-Epics.md` §1/§2.

## 0. Querying the Jira board (JQL)

MCP (`mcp_atlassian_*`) is the only Jira interface — there is no separate raw-API path. Every query must do its filtering and sorting **in the JQL itself**, not by pulling a broad result set and post-processing it locally:

- **Sort by priority, not by pulling-then-sorting**: `ORDER BY priority DESC, key` (or `status`) directly in the JQL string. Never fetch unsorted results intending to re-sort them yourself.
- **Filter by priority/label/status in JQL**, e.g. `project = SH AND priority = Highest AND status = "To Do"`, instead of fetching everything and filtering after the fact.
- **Trim `fields`** to just what's needed (`summary`, `status`, `priority`, `labels`, `parent`, `issuetype`) — Jira's API will still nest a full parent-Epic sub-object inside every Story regardless, so large boards can still produce a big response.
- Common queries: board overview — `project = SH AND status != Done ORDER BY priority DESC, status`; open stories in priority order — `project = SH AND issuetype = Story AND status = "To Do" ORDER BY priority DESC, key`; a specific Epic's open stories — `project = SH AND parent = "<EPIC-KEY>" AND status = "To Do" ORDER BY priority DESC, key`.
- Project SH's actual workflow (confirmed 2026-08-26, re-verify with `mcp_atlassian_getTransitionsForJiraIssue` if it looks stale): **To Do → In Progress → Review → Done**. No separate Design/Dev/Docs/Backlog status exists via this API — see the known tool-gap below.
- A local script (bash/python) to parse a tool's own JSON output is a documented last resort only — justified solely when a single MCP response is too large to usefully inspect directly, and even then it must only read/group data already returned by an MCP call, never substitute for one. If you find yourself reaching for a script to filter or sort, move that filter/sort into the JQL instead.

**Known tool-gap (confirmed 2026-08-26)**: moving an issue from Backlog onto the active Kanban Board is **not** possible via the available Atlassian MCP tools — they wrap Jira's core REST API v3 (issues, search, transitions, comments, links), not the Agile REST API's backlog/board endpoints. Do not attempt to work around this by guessing at a hidden field via `editJiraIssue`; it will not work and risks corrupting issue data. In practice: tell the user exactly which issues to manually drag from Backlog onto the Board in the Jira UI. Status transitions (below) work fine via API regardless of board membership — you can transition an issue to In Progress even before it's confirmed to be visually on the board.

## 1. Branch naming and creation

`<type>/SH-<epicNum>-<storyNum1>[-<storyNum2>...]-<slug>`
- `<type>` = `feature/` (new capability, default) or `bugfix/` (fixing something discovered/broken).
- `<epicNum>` = the parent Epic's numeric ID, `<storyNum>` = each story's numeric ID (hyphen-separated for a batch).
- `<slug>` = short kebab-case description from the story summary.
- Example: `feature/SH-5-16-author-design-skill` (single story), `feature/SH-2-10-14-env-setup` (batch).

Create with `git checkout -b <branch-name> main` before any implementation. **Never implement directly on `main`.** If a branch already exists for this story (check `git branch -a` first — the story may already be in progress from an earlier session), reuse it rather than creating a duplicate.

## 2. Committing

Called on request, any number of times per story — content agents (`Design-agent`/`Developer-agent`/`Reviewer-agent`/`Documenter-agent`) never commit their own work; whatever's currently in the working tree when asked gets staged and committed as-is. Message references the Jira key(s) (e.g. `[SH-16] add rolling-window macro`). Push (`git push -u origin <branch-name>`) only when asked to, or when a PR already exists and needs the new commit reflected.

## 3. Attaching a comment to a Jira issue

Use `mcp_atlassian_addCommentToJiraIssue`. This is normally done **once per story**, at the back-door step (opening the PR) — a single consolidated comment covering the design doc path (if one exists), a summary of the review outcome, and the PR link — not one comment per stage. Jira comments support Atlassian Document Format (ADF); a plain-text/markdown-ish body via the tool's text parameter is sufficient, don't over-engineer rich formatting. If circumstances call for an update after the fact (e.g. the design doc changed post-merge), add a **new** comment noting what changed rather than editing the old one — comments are an append-only audit trail.

## 4. Opening a PR

```
gh pr create --title "[SH-<key1>, SH-<key2>, ...] <summary>" --body "..."
```
Title always leads with the Jira key(s) in brackets. Body should summarize what changed, link back to the FR-ID/LLD traceability, and reference the frozen design doc path if one exists for this story. Push the branch first (`git push -u origin <branch-name>`) if it hasn't been pushed yet. Check `gh auth status` before attempting — if not authenticated, tell the user rather than failing silently (this has happened before in this project; `gh auth login` is an interactive browser flow the user must run themselves).

## 5. Status transitions

**Always call `mcp_atlassian_getTransitionsForJiraIssue` first** — do not hardcode transition IDs. This project's workflow has evolved before (a `Review` status was added mid-project, shifting IDs), and will likely evolve again. Current known statuses for project SH: To Do → In Progress → Review → Done. Only move a story to **Review** once a PR is actually open — never mark Review before that exists. Only move Review → **Done** once the user confirms the PR is merged — never merge a PR yourself.
