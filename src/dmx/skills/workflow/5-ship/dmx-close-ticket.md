---
name: close-ticket
title: Close Ticket
description: After a PR is merged, closes out the ticket externally — transitions the ticket to Done, adds the PR link as a comment, deletes the branch locally and remotely, and raises hotfix back-merge PRs if needed. Makes no changes to .dmx/ files.
arguments:
  - name: ticket_id
    description: Ticket identifier. Auto-detected from the current branch name if omitted.
    required: false
  - name: branch
    description: Branch name to close out. Defaults to the current branch.
    required: false
---

You are closing out a merged ticket. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → integration branch for checkout and back-merge
- `production_branch` → production branch (hotfix merge target)
- `cloud_id`, `atlassian_domain` → Jira coordinates (only when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

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

## Step 2 — Detect the working branch

If `{{branch}}` was provided, use it. Otherwise run `git branch --show-current`.

Store as `close_branch`.

Guard: if `close_branch` is a protected branch — `master`, `main`, `staging`, `development`, `{config.branch_base}`, or `{config.production_branch}` — stop: "Cannot close a protected branch."

## Step 3 — Detect the ticket reference

If `{{ticket_id}}` was provided, use it.

If not, extract from `close_branch`:
- Jira: match `DM-[0-9]+`, uppercase
- GitHub Issues: match `gh-([0-9]+)`, extract number
- none: no ticket ref

If no ref is found, continue without ticket steps (skip Steps 5 and 6).

## Step 4 — Find the merged PR

Call `list_pull_requests` on `user-github`:
```
owner:  {config.owner}
repo:   {config.repo}
state:  closed
head:   {config.owner}:{close_branch}
```

Filter to PRs where `merged_at` is not null. Store `html_url` and `base.ref`.

If no merged PR is found, warn: "No merged PR found for `{close_branch}`. Proceeding with branch deletion only — ticket will not be updated."

## Step 5 — Transition ticket to Done

**If `ticketing` is `jira`:**
1. Call `getTransitionsForJiraIssue` on `user-atlassian` with `{config.cloud_id}` and ticket ID.
2. Find transition named `Done`. Call `transitionJiraIssue`.

**If `ticketing` is `github-issues`:**
Call `update_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {ticket number}
state:        closed
```

**If `ticketing` is `none`:** Skip.

## Step 6 — Add PR link as ticket comment

**If `ticketing` is `jira`** and a merged PR was found:
Call `addCommentToJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {ticket id}
body:         "PR merged: {PR URL}"
```

**If `ticketing` is `github-issues`** and a merged PR was found:
Call `create_issue_comment` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {ticket number}
body:         "PR merged: {PR URL}"
```

**If `ticketing` is `none`:** Skip.

## Step 7 — Delete the branch locally

Run:
```
git checkout {config.branch_base}
git branch -D {close_branch}
```

If the PR is confirmed merged (Step 4), `git branch -D` is safe — the code is already on the base branch. If no merged PR was found, use `git branch -d` and do not force-delete.

## Step 8 — Delete the branch on origin

Run:
```
git push origin --delete {close_branch}
```

If the remote branch no longer exists, ignore the error.

## Step 9 — Hotfix back-merge check

If `base.ref` from Step 4 is `{config.production_branch}`:

Tell the user: "Hotfix merged to {config.production_branch}. Raising back-merge PRs..."

Call `create_pull_request` on `user-github` for `{config.production_branch} → {config.branch_base}`:
```
owner:  {config.owner}
repo:   {config.repo}
title:  chore | Back-merge {config.production_branch} into {config.branch_base} after {close_branch}
body:   "Hotfix `{close_branch}` was merged to {config.production_branch}. This PR brings those changes into {config.branch_base}."
head:   {config.production_branch}
base:   {config.branch_base}
draft:  false
maintainer_can_modify: true
```

If a `development` branch exists, raise a second PR for `{config.production_branch} → development` with the same pattern.

## Step 10 — Return the result

```
Ticket {ticket_ref} closed.

  Branch:  {close_branch} deleted locally and on origin
  Ticket:  {ticket_ref} → Done
  PR link: added to ticket

{if hotfix} Back-merge PRs raised:
  - {config.production_branch} → {config.branch_base}: {PR URL}
  - {config.production_branch} → development: {PR URL}

Next:
  - Run /dmx/create-ticket to start the next piece of work.
```

## Guards

- Never close a protected branch.
- If the PR is not confirmed merged, warn before deleting the branch. Do not assume the work is done.
- Make no changes to any `.dmx/` files — this skill is external-cleanup only. Memory learnings were already synced by `/dmx/create-pr`.
