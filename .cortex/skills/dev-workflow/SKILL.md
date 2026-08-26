---
name: dev-workflow
description: "Purely mechanical git/PR/Jira procedures for working a story once selected: branch naming/creation, attaching a document to a Jira issue, opening a PR, and status transitions. Referenced by the Design/Developer/Reviewer/Documenter/Triage agents for these repetitive steps — not a decision-making skill. Triggers: create a branch for this story, attach this doc to the Jira issue, open the PR, move this story to review."
---

# Dev Workflow (mechanical procedures)

This skill has no judgment calls — it's the reusable "how" for git/Jira mechanics that every SnowComotive agent needs, so those agents' own prompts can stay focused on their actual expertise (design, code, review, docs) instead of re-deriving git incantations each time. Agents reference this file and follow it; it does not decide *what* to build or *when* to move to the next stage — that's the agent's/user's call.

Traces to: FR-SDLC-02, `docs/05-Epics.md` §1/§2.

## 1. Branch naming and creation

`<type>/SH-<epicNum>-<storyNum1>[-<storyNum2>...]-<slug>`
- `<type>` = `feature/` (new capability, default) or `bugfix/` (fixing something discovered/broken).
- `<epicNum>` = the parent Epic's numeric ID, `<storyNum>` = each story's numeric ID (hyphen-separated for a batch).
- `<slug>` = short kebab-case description from the story summary.
- Example: `feature/SH-5-16-author-design-skill` (single story), `feature/SH-2-10-14-env-setup` (batch).

Create with `git checkout -b <branch-name> main` before any implementation. **Never implement directly on `main`.** If a branch already exists for this story (check `git branch -a` first — the story may already be in progress from an earlier session), reuse it rather than creating a duplicate.

## 2. Attaching a document to a Jira issue

Use `mcp_atlassian_addCommentToJiraIssue` with a comment body that includes the repo-relative path (e.g. `docs/designs/SH-16-author-design-agent.md`) and a one-line summary of what it contains. Jira comments support Atlassian Document Format (ADF) — a plain-text/markdown-ish body via the tool's text parameter is sufficient; don't over-engineer rich formatting. If the doc is updated later (e.g. by Documenter-agent after Reviewer-agent finds drift), add a **new** comment noting what changed rather than editing the old one — comments are an append-only audit trail of the story's history.

## 3. Opening a PR

```
gh pr create --title "[SH-<key1>, SH-<key2>, ...] <summary>" --body "..."
```
Title always leads with the Jira key(s) in brackets. Body should summarize what changed, link back to the FR-ID/LLD traceability, and reference the frozen design doc path if one exists for this story. Push the branch first (`git push -u origin <branch-name>`) if it hasn't been pushed yet. Check `gh auth status` before attempting — if not authenticated, tell the user rather than failing silently (this has happened before in this project; `gh auth login` is an interactive browser flow the user must run themselves).

## 4. Status transitions

**Always call `mcp_atlassian_getTransitionsForJiraIssue` first** — do not hardcode transition IDs. This project's workflow has evolved before (a `Review` status was added mid-project, shifting IDs), and will likely evolve again. Current known statuses for project SH: To Do → In Progress → Review → Done. Only move a story to **Review** once a PR is actually open (Documenter-agent's/Triage-agent's job) — never mark Review before that exists. Only move Review → **Done** once the user confirms the PR is merged — never merge a PR yourself.
