---
name: create-pr
title: Create Pull Request
description: Open a GitHub pull request with the correct title format and drafted description, sync the memory bank, then transition the ticket to In Review. Works with Jira, GitHub Issues, or no ticketing system.
arguments:
  - name: description
    description: PR body markdown. If omitted, dmx-draft-pr-description is called automatically.
    required: false
  - name: base
    description: Target branch. Defaults to branch_base from config. Use master only for hotfixes.
    required: false
  - name: draft
    description: "Set to true to open the PR as a draft. Defaults to false."
    required: false
  - name: ticket_id
    description: Ticket identifier. Auto-detected from the current branch name if omitted.
    required: false
---

You are opening a GitHub pull request. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → default base branch
- `cloud_id`, `project_key` → Jira coordinates (only needed when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates (fall back to parsing `git remote get-url origin`)

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve defaults

- If `{{base}}` was not provided, use `{config.branch_base}`.
- If `{{draft}}` was not provided, use `false`.

## Step 3 — Detect the ticket reference

If `{{ticket_id}}` was provided, use it.

If not, run `git branch --show-current` and extract the ticket ref:
- Jira: match `DM-[0-9]+` (case-insensitive), uppercase result
- GitHub Issues: match `gh-([0-9]+)`, extract the number
- none: no ticket ref

## Step 4 — Full memory bank sync

Sync durable learnings from the current ticket before opening the PR. Follow the same extraction logic as `/dmx/update-memory`:

Read all core memory bank files:
- `.dmx/projectbrief.md`
- `.dmx/productContext.md`
- `.dmx/systemPatterns.md`
- `.dmx/techContext.md`
- `.dmx/activeContext.md`

If a ticket ref was found in Step 3, read:
- `.dmx/tickets/active/{ticket_ref}/spec.md`
- `.dmx/tickets/active/{ticket_ref}/tasks.md`

Extract and write only durable, cross-ticket knowledge:

**`systemPatterns.md`** — architectural patterns, design decisions with rationale, changed component relationships.

**`techContext.md`** — new dependencies and why, new environment variables, new constraints.

**`productContext.md`** — user-facing behaviour changes, new capabilities or flows.

**`activeContext.md`** — refresh current state: active ticket, recently completed work, current focus, open decisions.

Do not extract:
- Implementation details that will change (specific function names, line numbers)
- Information already present in the files
- Notes only relevant to this ticket

Update each file with targeted additions only. Do not rewrite. Do not delete existing content.

## Step 5 — Commit memory changes

Run:
```
git status --short .dmx/
```

If any files under `.dmx/` were modified in Step 4:
```
git add .dmx/
git commit -m "chore: sync memory bank for {ticket_ref or branch name}"
```

If nothing changed, continue without committing.

## Step 6 — Check for uncommitted changes

Run:
```
git status --short
```

If there are any uncommitted changes (modified, staged, or untracked files relevant to the work), stop:
```
You have uncommitted changes. Run /dmx/commit before opening the PR.
```

Do not proceed until the working tree is clean.

## Step 7 — Verify the branch is pushed

Run `git status -sb`. If the branch has no remote tracking or is ahead of origin, run:
```
git push -u origin HEAD
```

## Step 8 — Draft the PR description

If `{{description}}` was provided, use it.

If not, invoke `dmx-draft-pr-description` with:
- `ticket_id`: {ticket ref from Step 3}
- `base`: `{{base}}`

## Step 9 — Fetch ticket details for the PR title

**If `ticketing` is `jira`:**
Call `getJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {ticket ref}
```
Extract `summary` and `issuetype.name`.

**If `ticketing` is `github-issues`:**
Call `get_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {ticket number extracted from ref}
```
Extract `title` as summary, infer type from labels or branch prefix.

**If `ticketing` is `none`:**
Derive a summary from the branch name: un-slugify the description segment (replace hyphens with spaces, title-case).

## Step 10 — Construct the PR title

Determine the type label:
- `Bug` if: issue type is Bug, OR branch starts with `bug-`
- `Feature` for everything else

| Provider | Title format |
|---|---|
| `jira` | `Feature\|Bug \| {TICKET_ID} {Summary In Title Case}` |
| `github-issues` | `Feature\|Bug \| #{number} {Summary In Title Case}` |
| `none` | `Feature\|Bug \| {Summary In Title Case}` |

## Step 11 — Create the pull request

Run `git branch --show-current` for the head branch.

Call `create_pull_request` on `user-github`:
```
owner:                 {config.owner}
repo:                  {config.repo}
title:                 {title from Step 10}
body:                  {description from Step 8}
head:                  {current branch}
base:                  {{base}}
draft:                 {{draft}}
maintainer_can_modify: true
```

If the call fails because a PR already exists, extract the existing URL and report it instead of failing.

## Step 12 — Transition ticket to In Review

**If `ticketing` is `jira`:**
1. Call `getTransitionsForJiraIssue` on `user-atlassian` with `{config.cloud_id}` and the ticket ID.
2. Find transition named `In Review`. Call `transitionJiraIssue`.

**If `ticketing` is `github-issues`:**
Call `update_issue` on `user-github` to add label `in-review` and remove `in-progress`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {ticket number}
labels:       ["in-review"]
```

**If `ticketing` is `none`:** Skip.

## Step 13 — Return the result

```
PR created: {PR URL}
{if ticketing ≠ none} Ticket: {ticket_ref} → In Review
{if memory committed in Step 5} Memory: synced and committed on this branch

Next:
  - Share the PR link for review.
  - Once merged, run /dmx/close-ticket to close out the ticket.
```
