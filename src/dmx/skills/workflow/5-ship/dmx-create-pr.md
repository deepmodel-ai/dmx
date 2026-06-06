---
name: create-pr
title: Create Pull Request
description: Open a GitHub pull request with the correct title format and drafted description, sync the memory bank, then transition the ticket to In Review. Works with Jira, GitHub Issues, or no ticketing system.
arguments:
  - name: description
    description: PR body markdown. If omitted, dmx-draft-pr-description is called automatically.
    required: false
  - name: base
    description: Target branch. Defaults to branch_base for features; auto-detects production_branch for hotfixes. Explicit value always wins.
    required: false
  - name: draft
    description: "Set to true to open the PR as a draft. Defaults to false."
    required: false
---

You are opening a GitHub pull request. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → integration branch (default target for feature PRs)
- `production_branch` → production branch (hotfix PR target)
- `cloud_id`, `project_key` → Jira coordinates (only needed when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates (fall back to parsing `git remote get-url origin`)

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve PR base and defaults

Read `.dmx/spec.md` if it exists — store for later steps.

Run `git branch --show-current` and store as `current_branch`.

**Resolve `base`:**

- If `{{base}}` was provided, use it — do not override an explicit argument.
- Else if spec frontmatter `type` is `hotfix`, or `.dmx/spec.md` contains `**Type:** hotfix`, or `current_branch` starts with `hotfix-`:
  - Resolve `production_branch` (see below) and use it as `base`.
- Else use `{config.branch_base}`.

**Resolve `production_branch` when needed** (hotfix PR base above, or when this skill requires it):

If `production_branch` is set in config, use it.

If missing from config:
```
git branch -a
```
Treat a name as present if it appears as a local branch or as `origin/{name}` / `remotes/origin/{name}`.
- If only `master` exists → `production_branch` = `master`
- If only `main` exists → `production_branch` = `main`
- If **both** `master` and `main` exist → stop: "Both master and main exist. Set `production_branch` in `.dmx/config.md` or re-run `/dmx/init`."
- If neither exists → stop: "`production_branch` not set in config. Run /dmx/init to configure it."

If `{{draft}}` was not provided, use `false`.

## Step 3 — Detect the ticket reference

Use `.dmx/spec.md` from Step 2 if already read; otherwise read it now. Extract the `ticket` field from YAML frontmatter. Also extract `summary`, `branch`, and `ticketing`.

If `spec.md` is absent or `ticket` is empty, fall back to parsing the current branch name:
- Jira: match `[A-Z]+-[0-9]+` (case-insensitive), uppercase result
- GitHub Issues: match `gh-([0-9]+)`, use `gh-{number}` as the ref
- none: no ticket ref

## Step 4 — Full memory bank sync

Sync durable learnings before opening the PR.

Read all of the following:
- `.dmx/projectbrief.md`
- `.dmx/productContext.md`
- `.dmx/systemPatterns.md`
- `.dmx/techContext.md`
- `.dmx/activeContext.md` — learning inbox (Open Learnings, Open Decisions, Session Notes)
- `.dmx/spec.md` — already read in Step 3
- `.dmx/tasks.md` — if it exists

**Promote all qualifying inbox items from `activeContext.md`:**

Scan `## Open Learnings` and `## Open Decisions`. For each item, ask: _"Is this durable beyond this branch?"_ If yes, append it to the appropriate core file:
- `systemPatterns.md` — architectural decisions, patterns, component relationships
- `techContext.md` — dependencies, env vars, constraints, tooling
- `productContext.md` — user-facing behaviour changes

Remove each promoted item from `activeContext.md` after writing it to the target file.

**Extract additional durable learnings** from `spec.md` and `tasks.md`:
- Completed phases or significant decisions documented in tasks/spec that belong in `systemPatterns.md` or `techContext.md`
- Any user-facing changes described in spec scope that should update `productContext.md`

**Refresh `activeContext.md`** — update `## Session Notes` with a line summarising the PR (e.g. "PR opened for: {summary}"). Do not write an `## Active Ticket` section.

Do not extract:
- Implementation details that will change (specific function names, line numbers)
- Information already present in the files
- Notes that are only relevant to this branch's spec

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
- `base`: `{{base}}`

The draft-pr-description skill will read `.dmx/spec.md` and `.dmx/tasks.md` directly for PR body content.

## Step 9 — Fetch ticket details for the PR title

**If `ticketing` is `jira`:**
Call `getJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {ticket ref}
```
Extract `summary` and `issuetype.name`.

**If `ticketing` is `github-issues`:**
Extract the numeric issue number from the ticket ref: `gh-{number}` → `{number}`.

Call `get_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {number}
```
Extract `title` as summary, infer type from labels or branch prefix.

**If `ticketing` is `none`:**
Use `summary` from the `spec.md` frontmatter (already read in Step 3). If spec is absent, derive from the branch name: un-slugify the description segment (replace hyphens with spaces, title-case).

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
issue_number: {number extracted from gh-{number} ref}
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
