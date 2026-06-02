---
name: draft-pr-description
title: Draft PR Description
description: Generate the pull request body from the current branch's commits, diff, and ticket. Outputs ready-to-use markdown following the standard PR template. Called automatically by create-pr, but available standalone to preview before opening a PR.
arguments:
  - name: ticket_id
    description: Ticket identifier. Auto-detected from the current branch name if omitted.
    required: false
  - name: base
    description: Base branch to diff against. Defaults to branch_base from config.
    required: false
---

You are drafting a pull request description. Follow every step in order. Output only the final markdown — no preamble or explanation.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → default base branch
- `atlassian_domain`, `cloud_id` → Jira coordinates (only when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve defaults

- If `{{base}}` was not provided, use `{config.branch_base}`.

## Step 3 — Detect the ticket reference

If `{{ticket_id}}` was provided, use it.

If not, run `git branch --show-current` and extract:
- Jira: match `DM-[0-9]+` (case-insensitive), uppercase result → store as `ticket_ref`
- GitHub Issues: match `gh-([0-9]+)`, extract number → store as `ticket_ref` = `gh-{number}`
- none: no ticket ref

## Step 4 — Fetch ticket details

**If `ticketing` is `jira`:**

Call `getJiraIssue` on `user-atlassian`:
```
cloudId:      {config.cloud_id}
issueIdOrKey: {ticket_ref}
```
Extract: `summary`, `description`, `issuetype.name`.
Store `ticket_url` = `https://{config.atlassian_domain}.atlassian.net/browse/{ticket_ref}`.

---

**If `ticketing` is `github-issues`:**

Call `get_issue` on `user-github`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {number from ticket_ref}
```
Extract: `title` → summary, `body` → description, `labels` → infer issue type.
Store `ticket_url` = `{html_url}`.

---

**If `ticketing` is `none`:**

Derive `summary` from the branch name: take the description segment, replace hyphens with spaces, title-case.
`description` = empty. `ticket_url` = none.

## Step 5 — Gather git context

Resolve the base ref (try in order, use the first that exists):
```
git rev-parse --verify origin/{{base}}
git rev-parse --verify {{base}}
```

If neither ref exists:
1. Tell the user: "`origin/{{base}}` was not found."
2. Ask them to paste the commit messages for this PR.
3. Stop until they reply. Use only their pasted messages for commit context in Steps 7–9.

If the base ref exists, compute the merge-base and collect commits:
```
git merge-base HEAD <base-ref>
git log <merge-base>..HEAD --oneline
git diff <merge-base>..HEAD --stat
```

Use only commits after the merge-base. Do not include commits on the base branch itself.

## Step 6 — Infer the type of change

Check each condition independently (multiple can apply):

- **New feature** — issue type is `Story` or `Task`, OR any commit starts with `feat`
- **Bug fix** — issue type is `Bug`, OR any commit starts with `fix`
- **Refactoring** — any commit starts with `refactor`
- **Security patch** — ticket has a `security` label, OR any commit contains `security`
- **UI/UX improvement** — any commit starts with `style`, or contains `ui` or `ux`

## Step 7 — Write the Description section

2–4 sentences covering:
1. What was changed (from commits and ticket summary)
2. Why it was changed (from ticket description and commit bodies)
3. Any context a reviewer needs to understand the approach

Do not copy-paste commit messages. Synthesise them into coherent prose.

## Step 8 — Write the "What is fixed / implemented?" section

Bullet list of meaningful changes. One bullet per logical change, not one per commit. Group related commits. Write from the reviewer's perspective.

## Step 9 — Write the Additional Information section

Scan the diff stat and commit messages for:
- New environment variables (`os.getenv`, `process.env`, `.env`, `settings.`)
- New dependencies (`requirements.txt`, `package.json`, `pyproject.toml`, `build.gradle`)
- Database migrations (`alembic`, `migrations/`, `flyway`)
- Deployment steps required

If any are detected, describe them specifically. If none, write `None`.

## Step 10 — Evaluate checklist items

Check each item independently against the diff and commit list:

- **Self-reviewed** — mark `[X]`: the PR description itself is evidence of review.
- **Tests added or updated** — mark `[X]` if the diff includes files matching `*test*`, `*spec*`, `tests/`, `__tests__/`, or test framework imports. Otherwise `[ ]`.
- **No new warnings or errors** — mark `[ ]`: cannot be verified from diff alone; leave for developer.
- **Environment variables updated** — mark `[X]` if diff includes `.env`, `settings.`, `os.getenv`, `process.env`. Otherwise `[ ]`.
- **New packages documented** — mark `[X]` if diff includes `package.json`, `requirements.txt`, `pyproject.toml`, `build.gradle`. Otherwise `[ ]`.
- **Tested on multiple devices/browsers** — mark `[ ]`: cannot be verified from diff alone; leave for developer.

Always use capital `X` for checked: `[X]`. Never invent evidence.

## Step 11 — Assemble the PR body

Output exactly this structure inside a single fenced code block. No text before or after the block.

```markdown
## {summary — rewrite if too long or technical}

{ticket_url — omit this line entirely if ticketing is none}

## Type of Change
- {[X] or [ ]} New feature
- {[X] or [ ]} Bug fix
- {[X] or [ ]} Refactoring
- {[X] or [ ]} Security patch
- {[X] or [ ]} UI/UX improvement

## Description
{2–4 sentences from Step 7}

## What is fixed / implemented?
{bullet list from Step 8}

## Additional Information
{content from Step 9}

## Screenshots (if applicable)
_N/A_

## Checklist
- {[X] or [ ]} I have performed a self-review of my code.
- {[X] or [ ]} I have added or updated relevant tests to cover the changes.
- {[X] or [ ]} My changes generate no new warnings or errors in development and production builds.
- {[X] or [ ]} Environment variables have been updated (if applicable).
- {[X] or [ ]} New packages have been installed and documented (if applicable).
- {[X] or [ ]} I have verified the changes on multiple devices and browsers (if applicable).
```

Output the markdown block above and nothing else.
