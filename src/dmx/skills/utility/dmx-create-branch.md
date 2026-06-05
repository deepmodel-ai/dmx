---
name: create-branch
title: Create Branch
description: Create a properly named branch, scaffold the ticket spec in the memory bank, and transition the ticket to In Progress. Works with Jira, GitHub Issues, or no ticketing system.
arguments:
  - name: ticket_id
    description: "Ticket identifier. Jira: DM-1662. GitHub Issues: 123. Omit when ticketing is none."
    required: false
  - name: type
    description: "Branch type prefix. Accepted values: feature, bug. Defaults to feature."
    required: false
  - name: description
    description: Short kebab-case description for the branch name. Auto-generated from the ticket title if omitted.
    required: false
---

You are creating a new branch and scaffolding the spec for active development. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → base branch (default: `staging` if not set)
- `cloud_id`, `project_key` → Jira coordinates (only needed when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve defaults

- If `{{type}}` was not provided, use `feature`.
- Accepted values: `feature`, `bug` only. Anything else — stop and ask the user to correct it.

## Step 3 — Fetch ticket details

**If `ticketing` is `jira`:**

If `{{ticket_id}}` was not provided, stop: "Jira ticketing is configured — provide a `ticket_id` (e.g. DM-1662)."

Call `getJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {{ticket_id}}
```
Extract: `summary`, `description`, `issuetype.name`.
Store `ticket_ref` = `{{ticket_id}}` (e.g. `DM-1662`).

---

**If `ticketing` is `github-issues`:**

If `{{ticket_id}}` was not provided, stop: "GitHub Issues ticketing is configured — provide an issue number (e.g. 123)."

Call `get_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {{ticket_id}}
```
Extract: `title` (→ summary), `body` (→ description), `number`.
Store `ticket_ref` = `gh-{number}` (e.g. `gh-123`).

---

**If `ticketing` is `none`:**

`{{ticket_id}}` is ignored. `summary` = `{{description}}` (required in this mode — if also omitted, stop and ask for a description). `description` = empty.
Store `ticket_ref` = `none`.

## Step 4 — Resolve the branch description

If `{{description}}` was provided, use it as-is (lowercase kebab-case).

If not provided and ticketing is `jira` or `github-issues`, slugify the `summary`:
- Lowercase all characters
- Replace spaces/underscores with hyphens
- Remove non-alphanumeric/hyphen characters
- Collapse consecutive hyphens, strip leading/trailing
- Truncate to 60 characters

## Step 5 — Construct the branch name

| Provider | Format | Example |
|---|---|---|
| `jira` | `{type}-{ticket_id_lowercase}-{description}` | `feature-dm-1662-add-rate-limiting` |
| `github-issues` | `{type}-gh-{number}-{description}` | `feature-gh-123-add-rate-limiting` |
| `none` | `{type}-{description}` | `feature-add-rate-limiting` |

## Step 6 — Create the remote branch

Run:
```
git remote get-url origin
```
Parse `owner` and `repo` if not already in config (HTTPS: last two path segments; SSH: split on `:`, right side, split on `/`).

Call `create_branch` on `user-github`:
```
owner:       {owner}
repo:        {repo}
branch:      {branch name from Step 5}
from_branch: {config.branch_base}
```

If branch already exists, stop: "Branch `{branch_name}` already exists on origin."

## Step 7 — Check out locally

Run:
```
git fetch origin
git checkout {branch_name}
```

## Step 8 — Scaffold the ticket spec

If `.dmx/spec.md` already exists, skip and note: "Spec already exists — skipping scaffold."

Write `.dmx/spec.md`:

```markdown
---
ticket: {ticket_ref or ""}
branch: {branch_name}
summary: {summary}
ticketing: {ticketing}
---

# {summary}

**Ticket:** {ticket link — see format below}
**Type:** {issue type or "feature"/"bug" from branch type}
**Branch:** {branch_name}

---

## Context
{1–3 sentences from ticket description. If none available, leave as placeholder.}

## Scope
{Bullet list of what is included. Derived from ticket description.}
- ...

## Out of Scope
{Anything explicitly excluded. "To be defined during Q&A." if unclear.}

## Technical Approach
{Leave blank — to be filled after Q&A.}

---

## Questions
<!-- Answer each question below before running /dmx/plan. -->

{2–5 clarifying questions based on ambiguities in the spec. Focus on:
  - Design decisions that would block implementation
  - Missing edge cases
  - Unclear integration points
  - Scope boundaries}

1. {Question}
   Answer:

2. {Question}
   Answer:
```

**Ticket link format:**
- Jira: `[{{ticket_id}}](https://{config.atlassian_domain}.atlassian.net/browse/{{ticket_id}})`
- GitHub Issues: `[#{number}]({html_url})`
- none: _(no ticketing system)_

When `ticketing` is `none`, omit `ticket` from frontmatter.

## Step 9 — Transition to In Progress

**If `ticketing` is `jira`:**
1. Call `getTransitionsForJiraIssue` on `user-atlassian` with `{config.cloud_id}` and `{{ticket_id}}`
2. Find transition named `In Progress`. Call `transitionJiraIssue`.

**If `ticketing` is `github-issues`:**
Call `add_issue_comment` on `user-github` (if tool available), or `update_issue` to add an `in-progress` label:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {{ticket_id}}
labels:       ["in-progress"]
```

**If `ticketing` is `none`:** Skip.

## Step 10 — Return the result

```
Branch created: {branch_name}
{if ticketing ≠ none} Ticket: {ticket_ref} → In Progress
Spec scaffolded: .dmx/spec.md

Next steps:
  1. Answer the questions in spec.md.
  2. Run /dmx/plan to generate the phased task plan.
  3. Run /dmx/implement-next-phase to begin implementation.
```

## Guards

- Never use for hotfixes. If the user mentions "hotfix" or "production incident", direct them to `/dmx/hotfix`.
- If `branch_base` in config is far behind `master`, warn: "Your base branch appears behind master — consider syncing before branching."
- If `.dmx/` does not exist, stop: "Memory bank not found. Run /dmx/init to set up this project."
- For `none` mode, if neither `{{ticket_id}}` nor `{{description}}` was provided, stop: "Provide a `description` when ticketing is disabled."
