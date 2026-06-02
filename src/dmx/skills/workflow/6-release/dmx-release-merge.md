---
name: release-merge
title: Release Merge (Staging → Master)
description: Open a pull request from staging to master as the formal release gate. Reads release notes from .dmx/releases/{version}.md if already drafted, otherwise generates them automatically. A human reviews and merges the PR before create-release is run.
arguments:
  - name: version
    description: Version number for this release, e.g. v0.14.0.
    required: true
---

You are creating the staging → master release merge PR. Follow every step in order. This PR is a gate for human review — you are not merging it yourself.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `owner`, `repo` → GitHub coordinates
- `branch_base` → should be `staging` or `main`

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Load or generate release notes

Check if `.dmx/releases/{{version}}.md` exists.

If it does, read it and store the content.

If it does not, invoke the `dmx-draft-release-note` prompt with `version: {{version}}`. It will generate and save the file. Store its output.

From the release notes extract:
- **Executive summary** — the opening paragraph
- **Change entries** — from the `What's Changed` sections (Features, Bug Fixes, Improvements, Security, Chores)
- **Ticket references** — any `DM-NNN` or `#NNN` references from each entry
- **Type coverage** — whether Features, Bug Fixes, etc. are present (for Type of Change checkboxes)

## Step 3 — Build the merge PR body

Construct the PR body using the extracted content. This is the engineering team's review document — different audience from the release notes.

**Type of Change** — always mark `Release merge` with `[X]`. Mark others `[X]` only if represented in the release notes:

```markdown
## Merge `{config.branch_base}` into `master` — {{version}}

## Type of Change
- [X] Release merge
- {[X] or [ ]} New feature
- {[X] or [ ]} Bug fix
- {[X] or [ ]} Refactoring
- {[X] or [ ]} Security patch
- {[X] or [ ]} UI/UX improvement

## Description

{executive summary paragraph from the release notes}

This PR merges `{config.branch_base}` into `master` for the **{{version}}** release.

## What is fixed / implemented?

| **Ticket** | **Type** | **Summary** |
|---|---|---|
| {ticket ref or PR number} | {Feature / Bug fix / Improvement / Chore} | {one-line summary} |

One row per ticket or PR. Use ticket ref (DM-NNN or #NNN) when present, otherwise use PR number.

### Highlights

{bullet list with **bold** lead-ins, grouped by theme. 3–6 bullets. Derived from the What's Changed entries. Example: `* **Agent Manager:** Complete CRUD API with lifecycle management and avatar support.`}

## Additional Information

{Aggregate from release notes: new env vars, new packages, migrations, breaking changes, manual deploy steps. If none, write a brief sentence about the release impact instead of "None".}

## Screenshots (if applicable)

_N/A_

## Checklist

- [ ] I have added or updated relevant tests to cover the changes.
- [ ] I have performed a self-review of my code.
- [ ] My changes generate no new warnings or errors in development and production builds.
- [ ] I have verified the changes on multiple devices and platforms.
- [ ] Environment variables have been updated (if applicable).
- [ ] New packages have been installed and documented (if applicable).
```

Store this as `pr_body`.

## Step 4 — Verify staging is ahead of master

Run:
```
git fetch origin
git log origin/master..origin/{config.branch_base} --oneline
```

If this returns no commits, stop:
"No commits ahead of master on {config.branch_base} — nothing to release. Ensure all feature PRs for this release have been merged first."

## Step 5 — Check for an existing release-merge PR

Call `list_pull_requests` on `user-github`:
```
owner:  {config.owner}
repo:   {config.repo}
state:  open
base:   master
head:   {config.owner}:{config.branch_base}
```

If an open PR already exists, stop:
"A release-merge PR already exists: {existing PR URL}. Update the description there if needed rather than opening a duplicate."

## Step 6 — Create the pull request

Call `create_pull_request` on `user-github`:
```
owner:                 {config.owner}
repo:                  {config.repo}
title:                 Release | {{version}} — {config.branch_base} → master
body:                  {pr_body from Step 3}
head:                  {config.branch_base}
base:                  master
draft:                 false
maintainer_can_modify: true
```

## Step 7 — Return the result

```
Release merge PR created: {PR URL}

Notes: .dmx/releases/{{version}}.md

Next:
  - Share the PR with reviewers.
  - Once approved and merged, run /dmx/create-release version:{{version}}
```

## Guards

- Never merge the PR. Your job ends when the PR URL is returned.
- If `create_pull_request` fails because a PR already exists, extract the URL from the error and report it instead of failing.
