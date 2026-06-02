---
name: draft-release-note
title: Draft Release Note
description: Generate structured release notes by diffing merged PRs and commits between the last published release and the current HEAD of the base branch. Enriches entries with ticket data from Jira, GitHub Issues, or PR titles alone.
arguments:
  - name: version
    description: Version number for this release, e.g. v0.14.0.
    required: true
  - name: since
    description: "Override the baseline tag to diff from. If omitted, the most recent git tag is used."
    required: false
---

You are drafting release notes for version `{{version}}`. Output only the final markdown — no preamble.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → the release branch (staging or main)
- `cloud_id`, `project_key` → Jira coordinates (only needed when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve the baseline tag

If `{{since}}` was provided, use it.

If not:
```
git fetch --tags origin
git describe --tags --abbrev=0 origin/{config.branch_base}
```

If no tags exist, use the initial commit SHA. Note `(initial release)` in output.

Store as `since_ref`.

## Step 3 — Fetch merged PRs since baseline

Call `list_pull_requests` on `user-github`:
```
owner:     {config.owner}
repo:      {config.repo}
state:     closed
base:      {config.branch_base}
per_page:  100
```

Filter to `merged_at` not null.

Cross-reference with:
```
git log {since_ref}..origin/{config.branch_base} --oneline --merges
```
to identify which PRs fall within the release range.

## Step 4 — Collect ticket references

For each PR, extract ticket references from the title and `head.ref`:
- Jira: pattern `{project_key}-[0-9]+` (e.g. `DM-1667`)
- GitHub Issues: pattern `#([0-9]+)` or `gh-([0-9]+)` in branch name
- none: no ticket extraction

Deduplicate the list.

## Step 5 — Enrich with ticket data

**If `ticketing` is `jira`:**
For each unique ticket ID, call `getJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {ticket_id}
```
Extract `summary` and `issuetype.name`. Build map: `ticket_id → { summary, type }`.
If a ticket cannot be retrieved, use the PR title and mark `(Jira unavailable)`.

**If `ticketing` is `github-issues`:**
For each unique issue number, call `get_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {number}
```
Extract `title` as summary, infer type from labels.

**If `ticketing` is `none`:**
Use PR titles directly as the entry text. Skip ticket enrichment.

## Step 6 — Supplement with git log

```
git log {since_ref}..origin/{config.branch_base} --oneline --no-merges
```

Collect commits not corresponding to a PR. Include as `Chores`.

## Step 7 — Categorise changes

| Category | Maps from |
|---|---|
| Features | Jira `Story`, GH issue label `feature`, commit prefix `feat` |
| Bug Fixes | Jira `Bug`, GH issue label `bug`, commit prefix `fix` |
| Improvements | Jira `Task`, commit prefix `refactor` or `perf` |
| Security | label `security`, commit prefix `fix(security)` |
| Chores | commit prefixes `chore`, `ci`, `build`, `docs`, `test` |

## Step 8 — Draft the executive summary

Write a single paragraph (2–4 sentences) summarising the release for someone who was not watching the tickets. Cover:
- The primary themes or headline features in this release
- Any notable improvements, fixes, or refactors worth calling out
- The overall impact — what does this release enable or improve?

Write in plain prose. Name feature areas using the categories from Step 7. Do not use bullet points. This paragraph goes at the top of the release notes.

Example shape (adapt to actual content):
> This release introduces {primary feature}, {secondary theme}, and {notable fix or improvement}. It also includes {chore or infrastructure theme} and several quality improvements across {area}.

## Step 9 — Assemble release notes

Omit sections with zero entries.

**Jira format:**
```markdown
# Release Notes — {{version}}

**Release date:** {today}
**Baseline:** {since_ref}

{executive summary paragraph from Step 8}

Highlights since {since_ref}:

## What's Changed

### Features
- {Jira summary} ([{ticket_id}](https://{config.atlassian_domain}.atlassian.net/browse/{ticket_id})) — #{PR number}

### Bug Fixes
...

### Improvements
...

### Security
...

### Chores
- {commit message} ({short SHA})

## Full Diff
[{since_ref}...{{version}}](https://github.com/{config.owner}/{config.repo}/compare/{since_ref}...{{version}})
```

**GitHub Issues format:**
```markdown
# Release Notes — {{version}}

**Release date:** {today}
**Baseline:** {since_ref}

{executive summary paragraph from Step 8}

Highlights since {since_ref}:

## What's Changed

### Features
- {issue title} ([#{number}]({issue_url})) — #{PR number}

...

## Full Diff
[{since_ref}...{{version}}](https://github.com/{config.owner}/{config.repo}/compare/{since_ref}...{{version}})
```

**None format:**
```markdown
# Release Notes — {{version}}

**Release date:** {today}
**Baseline:** {since_ref}

{executive summary paragraph from Step 8}

Highlights since {since_ref}:

## What's Changed

### Features
- {PR title} — #{PR number}

...

## Full Diff
[{since_ref}...{{version}}](https://github.com/{config.owner}/{config.repo}/compare/{since_ref}...{{version}})
```

## Step 10 — Save release notes to file

Create `.dmx/releases/` if it does not exist.

Write the assembled release notes to `.dmx/releases/{{version}}.md`.

## Step 11 — Return the result

Output:
```
Release notes drafted: .dmx/releases/{{version}}.md
{N} changes across {categories list}.

Review and edit the file, then:
  - Run /dmx/release-merge version:{{version}} to open the staging → master PR.
```
