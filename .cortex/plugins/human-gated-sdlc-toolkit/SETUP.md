# Setup: `human-gated-sdlc-toolkit`

This plugin ships six agents (`design-agent`, `developer-agent`, `reviewer-agent`,
`documenter-agent`, `jira-triage-agent`, `genesis-agent`) and one skill
(`dev-workflow`). None of them hardcode a specific project's Jira details — they
read them from **your project's own `AGENTS.md`**, which CoCo auto-loads into
every agent's context as "Project Instructions." This plugin never ships a copy
of any project's `AGENTS.md` — that file is yours, per-project, always.

## Required fields in your project's `AGENTS.md`

For `jira-triage-agent` and `dev-workflow` to work, your `AGENTS.md` needs to state:

| Field | Example | Used for |
|---|---|---|
| Jira cloud ID | `11111111-2222-3333-4444-555555555555` | every `mcp_atlassian_*` call |
| Jira site | `yourcompany.atlassian.net` | every `mcp_atlassian_*` call |
| Jira project key | `PROJ` | JQL filters, branch names, commit messages, PR titles — substituted wherever `dev-workflow` and `jira-triage-agent` show `<PROJECT_KEY>` |

If you don't have these yet, write clearly-marked placeholders instead
(`<JIRA_CLOUD_ID>`, `<JIRA_SITE>`, `<JIRA_PROJECT_KEY>`) — `jira-triage-agent` is
designed to ask you for the real values rather than guess or carry over a value
from a different project.

## Two ways to get there

1. **Run `genesis-agent` first** (recommended for a brand-new project). Its
   Stage 5 asks for these three fields directly and writes them into a fresh
   `AGENTS.md` it creates for you, alongside the BRD/FRD/HLD/LLD doc stack it
   brainstorms and freezes with you.
2. **Add them to an existing `AGENTS.md` yourself** (if you already have a
   project and just want the SDLC agents). Add a short "Jira configuration"
   section with the three fields above — no specific format required, the
   agents just need to find the values somewhere in context.

## Optional: label-tier tiebreak

`jira-triage-agent`'s priority model uses the Jira `priority` field as the
primary signal, with an optional label-tier (`P0`-`P3` or similar) tiebreak when
`priority` is unset. If your project doesn't use label tiers, nothing needs
configuring — the agent falls back to `priority` alone.

## What this plugin does not do

It does not create or manage your Jira project, does not configure the
`mcp_atlassian_*` MCP connection itself (that's a separate Atlassian
integration setup, outside this plugin's scope), and does not write to your
project's `AGENTS.md` on your behalf unless you run `genesis-agent`.
