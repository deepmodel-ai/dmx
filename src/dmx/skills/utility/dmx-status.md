---
name: status
title: Development Status
description: Show a snapshot of your in-progress tickets and open pull requests. Works with Jira, GitHub Issues, or no ticketing system.
arguments:
  - name: repo_only
    description: "If true, show only GitHub PR status and skip tickets. Defaults to false."
    required: false
---

You are generating a development status report. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `owner`, `repo` → GitHub coordinates (fall back to parsing `git remote get-url origin`)
- `cloud_id`, `project_key` → Jira coordinates (only needed when ticketing is `jira`)
- `github_username` → GitHub username (only needed when ticketing is `github-issues`)

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve defaults

- If `{{repo_only}}` was not provided, use `false`.

## Step 3 — Fetch current user info

**If `ticketing` is `jira`** and `{{repo_only}}` is false:
Call `atlassianUserInfo` on `user-atlassian`. Extract `account_id` and `displayName`.

**All others:** Skip.

## Step 4 — Fetch in-progress tickets

Skip if `{{repo_only}}` is `true` or `ticketing` is `none`.

**If `ticketing` is `jira`:**
Call `searchJiraIssuesUsingJql` on `user-atlassian`:
```
cloudId:    {config.cloud_id}
jql:        project = {config.project_key} AND assignee = currentUser() AND status in ("In Progress", "In Review") ORDER BY updated DESC
fields:     ["summary", "status", "issuetype", "priority", "updated"]
maxResults: 25
```

**If `ticketing` is `github-issues`:**
Call `list_issues` on `user-github`:
```
owner:    {config.owner}
repo:     {config.repo}
state:    open
assignee: {config.github_username}
```
Filter to issues with label `in-progress` or `in-review`.

## Step 5 — Fetch open pull requests

Run `git remote get-url origin` to confirm owner/repo if not in config.

Call `list_pull_requests` on `user-github`:
```
owner:     {config.owner}
repo:      {config.repo}
state:     open
per_page:  50
```

## Step 6 — Detect the current branch

Run `git branch --show-current`.

## Step 7 — Assemble the report

```
# Development Status — {repo}
Current branch: {branch}

---

## My Tickets
{if ticketing is none or repo_only: (ticketing disabled — showing PRs only)}

{for each Jira ticket:}
  [{IN PROGRESS|IN REVIEW}] {KEY} — {summary}
  https://{config.atlassian_domain}.atlassian.net/browse/{KEY}

{for each GitHub issue:}
  [{IN PROGRESS|IN REVIEW}] #{number} — {title}
  {html_url}

{if none found: (no tickets currently In Progress or In Review)}

---

## Open Pull Requests in {repo}
{for each PR:}
  #{number} {title}
  Branch: {head.ref} → {base.ref}
  Author: {user.login}
  Opened: {created_at as Month D, YYYY}
  URL: {html_url}
  {[DRAFT] if draft} {[APPROVED] if approved}

{if none: (no open pull requests)}

---

## Local Branch Summary
{if current branch is not a protected branch:}
Commits ahead of {branch_base}: {count from git log origin/{branch_base}..HEAD --oneline | wc -l}
```

Output only the block above. No commentary.
