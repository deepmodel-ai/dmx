---
name: hotfix
title: Start Hotfix
description: Create a hotfix branch from master for a production incident, scaffold the spec, and transition the ticket to In Progress. Works with Jira, GitHub Issues, or no ticketing system.
arguments:
  - name: ticket_id
    description: "Ticket identifier for the production incident. Jira: DM-1662. GitHub Issues: 123. Omit when ticketing is none."
    required: false
  - name: description
    description: Short kebab-case description for the branch name. Auto-generated from the ticket title if omitted.
    required: false
---

You are starting a hotfix for a production incident. Hotfix branches are always created from `master`, not `branch_base`. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → used for back-merge target after hotfix is merged
- `cloud_id` → Jira cloud ID (only needed when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Fetch ticket details

**If `ticketing` is `jira`:**
If `{{ticket_id}}` was not provided, stop: "Provide a `ticket_id` for the production incident."
Call `getJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {{ticket_id}}
```
Extract `summary`, `description`, `issuetype.name`.
Store `ticket_ref` = `{{ticket_id}}`.

---

**If `ticketing` is `github-issues`:**
If `{{ticket_id}}` was not provided, stop: "Provide a GitHub issue number for the production incident."
Call `get_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {{ticket_id}}
```
Extract `title` as summary, `body` as description.
Store `ticket_ref` = `gh-{number}`.

---

**If `ticketing` is `none`:**
`{{ticket_id}}` is ignored. `summary` = `{{description}}` (required — if omitted, stop and ask).
Store `ticket_ref` = `none`.

## Step 3 — Resolve the branch description

If `{{description}}` was provided, use it.

If not, slugify the `summary` (lowercase, hyphens, truncate to 60 characters).

## Step 4 — Construct the branch name

| Provider | Format | Example |
|---|---|---|
| `jira` | `hotfix-{ticket_id_lowercase}-{description}` | `hotfix-dm-1662-fix-token-expiry` |
| `github-issues` | `hotfix-gh-{number}-{description}` | `hotfix-gh-99-fix-token-expiry` |
| `none` | `hotfix-{description}` | `hotfix-fix-token-expiry` |

## Step 5 — Create the remote branch from master

Call `create_branch` on `user-github`:
```
owner:       {config.owner}
repo:        {config.repo}
branch:      {branch name}
from_branch: master
```

If branch already exists, stop: "Branch `{branch_name}` already exists."

## Step 6 — Check out locally

Run:
```
git fetch origin
git checkout {branch_name}
```

## Step 7 — Scaffold spec.md

Write `.dmx/spec.md` (skip if it already exists):

```markdown
---
ticket: {ticket_ref or ""}
branch: {branch_name}
summary: {summary}
ticketing: {ticketing}
---

# {summary}

**Ticket:** {ticket link or "(no ticketing system)"}
**Type:** hotfix
**Branch:** {branch_name}

---

## Context
{1–3 sentences from ticket description — what the production incident is}

## Scope
{What this fix addresses}

## Out of Scope
{Related issues not addressed in this hotfix}

## Technical Approach
{Leave blank — to be filled after Q&A.}

---

## Questions
<!-- Answer each question below before implementing. -->

1. {Question}
   Answer:

2. {Question}
   Answer:
```

**Ticket link format:**
- Jira: `[{ticket_ref}](https://{config.atlassian_domain}.atlassian.net/browse/{ticket_ref})`
- GitHub Issues: `[#{number}]({html_url})`
- none: _(no ticketing system)_

When `ticketing` is `none`, omit `ticket` from frontmatter.

## Step 8 — Transition to In Progress

**If `ticketing` is `jira`:**
Get transitions, find `In Progress`, call `transitionJiraIssue`.

**If `ticketing` is `github-issues`:**
Add `in-progress` label via `update_issue`.

**If `ticketing` is `none`:** Skip.

## Step 9 — Return the result and workflow reminder

```
Hotfix branch created: {branch_name}
Based on: master
{if ticketing ≠ none} Ticket: {ticket_ref} → In Progress
Spec: .dmx/spec.md

Hotfix workflow:
  1. Implement the fix (use /dmx/implement-next-phase or /dmx/implement-next-task).
  2. Commit and push.
  3. Open a PR to master (not {branch_base}): /dmx/create-pr with base: master
  4. After the PR is merged, run /dmx/cleanup.
     → cleanup will automatically raise back-merge PRs to {branch_base} and development.
  5. Create a release: /dmx/create-release
```
