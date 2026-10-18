---
name: derive-ticket
title: Derive Ticket — Formalise Uncommitted Work
description: Reads uncommitted changes in the working tree, infers what was built, creates a ticket in the configured provider, creates a properly named branch, stashes and re-applies changes if on a protected branch, and scaffolds a derived spec.md.
arguments:
  - name: type
    description: Branch type prefix. feature | bug. Defaults to feature.
    required: false
---

GitHub calls in this skill go to `user-github`. When `.dmx/config.md` has `github_access: gh`, or that server isn't connected, run the `gh` equivalent from the dmx rule's **GitHub access** table instead.

You are formalising work that has already been started. The developer has uncommitted changes and needs a ticket, branch, and spec created from what they have built. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → integration branch
- `production_branch` → production branch (when set in config)
- `cloud_id`, `project_key` → Jira coordinates (only when ticketing is `jira`)
- `atlassian_domain` → Jira domain (only when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init first."

## Step 2 — Resolve defaults

If `{{type}}` was not provided, use `feature`. Accepted values: `feature`, `bug` only.

## Step 3 — Read uncommitted changes

Run:
```
git status --porcelain
git diff
git diff --cached
```

Collect all of:
- Modified tracked files (staged and unstaged)
- New untracked files (`git status --porcelain` lines starting with `??`)

If all three are empty (working tree is clean and nothing staged), stop: "No uncommitted changes found. Use /dmx/create-ticket to start new work instead."

Record `original_branch` from `git branch --show-current`.

## Step 4 — Detect branch situation

Classify `original_branch`:

**Protected branch** (`master`, `main`, `staging`, `development`, or `{config.branch_base}` or `{config.production_branch}` when set in config):
The developer is working directly on a protected branch. Changes must be moved to a new branch.
Set `needs_stash = true`.

**Unnamed or temporary branch** (any other name):
The developer is on an ad-hoc branch. A properly named branch will be created from the base, and changes will be moved there.
Set `needs_stash = true`.

In all cases, `needs_stash = true` — changes will be stashed, a new branch created, and the stash re-applied.

## Step 5 — Analyse the changes

Read the diff and file list from Step 3. Infer:

- **What was built:** A concise description of the change (what it does, not how).
- **Why:** The likely problem being solved or feature being added.
- **Type:** `feature` if new capability; `bug` if a fix — override `{{type}}` if the diff clearly indicates otherwise.
- **Affected areas:** List the files and modules touched.

Use this analysis to draft ticket content in the next step.

## Step 6 — Draft ticket content

### Summary
Single sentence, maximum 80 characters, imperative present tense, capital first letter, no trailing period.

### Description
```markdown
## Context
{1–3 sentences: what problem this change addresses}

## What Was Built
{Bullet list of what was implemented, derived from the diff}
- ...

## Acceptance Criteria
{Verifiable conditions derived from the changes already made}
- [ ] ...

## Notes
{Any constraints or follow-up considerations visible in the diff}
```

## Step 7 — Create the ticket

**Destination** (GH-92). When `.dmx/config.md` has `jira_projects` or `github_issue_repos`, or a component sets `ticket` in its `component.yaml`, decide the ticket's components first (see **Components.** in the spec step), then call `ticket_destination` on `user-dmx` with `requested` set to the project or repo the developer named, if any, and `components` set to those components. If `blocked` is set, show it and ask which allowed destination to use. If `confirm` is true, ask its `question` and wait; when the developer picks another, call it again with that as `requested`. Then use its `target`: as `projectKey` for Jira, or as `owner` and `repo` for GitHub, with its `labels` added to the issue's labels. For a GitHub repo other than the default, store `ticket_ref` as its `short_ref` with the number filled in (such as `ledger-issues-7`), use that in the branch name in place of `gh-{number}`, add `issue_repo: {target}` to the spec frontmatter after `ticket`, and refer to the issue by its `full_ref` (such as `acme/ledger-issues#7`). Otherwise everything below applies unchanged.

**If `ticketing` is `jira`:**

Call `atlassianUserInfo` on `user-atlassian` to get `account_id`.

Call `createJiraIssue` on `user-atlassian`:
```
cloudId:              {config.cloud_id}
projectKey:           {config.project_key}
issueTypeName:        {resolved type → "Task" for feature, "Bug" for bug}
summary:              {summary from Step 6}
description:          {description from Step 6}
assignee_account_id:  {account_id}
contentFormat:        markdown
```
Store `ticket_ref` = returned key (e.g. `DM-1667`).

---

**If `ticketing` is `github-issues`:**

Call `create_issue` on `user-github`:
```
owner:   {config.owner}
repo:    {config.repo}
title:   {summary from Step 6}
body:    {description from Step 6}
labels:  ["{resolved type}"]
```
Store `ticket_ref` = `gh-{number}`.

---

**If `ticketing` is `none`:**

`ticket_ref` = `none`. Proceed without creating a ticket.

## Step 8 — Construct the branch name

Slugify the summary (lowercase, hyphens, no special characters, max 60 chars).

| Provider | Format | Example |
|---|---|---|
| `jira` | `{type}-{ticket_id_lowercase}-{slug}` | `feature-dm-1667-add-rate-limiting` |
| `github-issues` | `{type}-gh-{number}-{slug}` | `feature-gh-123-add-rate-limiting` |
| `none` | `{type}-{slug}` | `feature-add-rate-limiting` |

## Step 9 — Stash changes, create branch, re-apply

Run:
```
git stash push -m "derive-ticket: {summary}"
```

Record the stash ref (e.g. `stash@{0}`).

Call `create_branch` on `user-github`:
```
owner:       {config.owner}
repo:        {config.repo}
branch:      {branch_name from Step 8}
from_branch: {config.branch_base}
```

Run:
```
git fetch origin
git checkout {branch_name}
git stash pop
```

If `git stash pop` fails with a conflict, stop: "Stash re-apply failed with conflicts. Resolve conflicts manually, then run `git stash drop` to clean up. Branch `{branch_name}` was created and checked out."

## Step 10 — Scaffold spec.md

Write the ticket's `spec.md` in its job folder: the `Ticket files:` path in the loop runtime instruction that invoked this skill, or `.dmx/jobs/{job_id}/spec.md` when run directly, where `{job_id}` is `{ticket_ref}` when it is a real ticket id, else `{branch_name}`. Create the folder if needed. Do not write `.dmx/spec.md`.

**Components.** When `.dmx/components/` exists, the `components` line lists the components this ticket is about, by the names of their folders in `.dmx/components/`. Choose them from the changed files from Step 3 and the ticket drafted in Step 6, using each `component.yaml`'s `description` and `paths`; a component whose `paths` match a changed file is always included. If it is unclear which, ask the developer before writing the spec. Write `components: []` for a ticket about repo-wide files only. Leave the line out when the repo has no `.dmx/components/` folder.

```markdown
---
ticket: {ticket_ref or ""}
branch: {branch_name}
summary: {summary}
ticketing: {ticketing}
components: [{component ids}]
---

# {summary}

**Ticket:** {ticket link}
**Type:** {type}
**Branch:** {branch_name}
**Derived from:** uncommitted changes ({original_branch})

---

## What Was Built
{Description derived from the diff — what was implemented, not what will be}

## Files Changed
{List of files from Step 3}

## Context
{Why this change was made — inferred from Step 5}

## Scope
{What is included in these changes}

## Out of Scope
{What was explicitly not changed — inferred from the diff}

---

## Validation Questions
<!-- Answer these before running /dmx/plan or continuing implementation. -->
<!-- These confirm the work already done is correct — not planning questions. -->

{2–4 questions validating key assumptions in the diff. Focus on:
  - Edge cases not visible in the current changes
  - Error handling that may be missing
  - Integration points with other parts of the system
  - Whether acceptance criteria are fully met}

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

## Step 11 — Transition ticket to In Progress

**If `ticketing` is `jira`:**
Call `getTransitionsForJiraIssue`, find `In Progress`, call `transitionJiraIssue`.

**If `ticketing` is `github-issues`:**
Call `update_issue` to add label `in-progress`.

**If `ticketing` is `none`:** Skip.

## Step 12 — Return the result

```
Ticket derived from uncommitted changes.

{if ticketing ≠ none} Ticket: {ticket_ref} — {summary}
Branch:  {branch_name}  (created from {branch_base})
Stash:   applied successfully
Spec:    .dmx/jobs/{job_id}/spec.md

Next steps:
  1. Review spec.md — verify the inferred scope and context are accurate.
  2. Answer the Validation Questions in spec.md.
  3. Run /dmx/plan to generate the phased task plan.
     Or run /dmx/implement-next-phase if the work is already complete and only needs tidying.
```

## Guards

- Never stash if the working tree is clean (Step 3 already handles this).
- If `git stash push` fails, stop before creating the branch: "Stash failed: {error}. Resolve before continuing."
- If the branch already exists on origin, stop: "Branch `{branch_name}` already exists. Either use that branch or provide a different description."
- Do not create a ticket summary longer than 80 characters. Fix it first.
- If `createJiraIssue` or `create_issue` returns an error, surface the full error and stop. Do not proceed to branch creation.
