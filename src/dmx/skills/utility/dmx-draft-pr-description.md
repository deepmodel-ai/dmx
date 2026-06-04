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

You are drafting a pull request description. Output only the final markdown — no preamble or explanation.

Follow the steps below to gather information, then fill in the output template determined in Step 1.

## Step 1 — Detect the output template

Check for a PR template in the workspace root (try each path, use the first that exists):
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/pull_request_template.md`
- `PULL_REQUEST_TEMPLATE.md`

**If a template file is found:** read it. Use it as the output structure — fill in each section with content generated from the commits, diff, and ticket in the steps below. Do not change the section headings or structure.

**If no template file is found:** use the following embedded template as the output structure:

```markdown
{If ticketing is not none: "Closes #{ticket_ref}" — omit this line entirely if ticketing is none}

## Summary

{1–2 sentences: what changed and why this approach was chosen}

## Review focus

{1–2 sentences: where to concentrate review attention; what to skim or skip}

## Changes

{bullet list grouped by concern — specific, from the reviewer's perspective}

## Validation

{[x] checked list of what was actually tested or verified}

## Notes

{non-obvious decisions, known issues, follow-up tickets, workarounds — or "None"}

## Checklist

- [x] Self-reviewed
- {[x] or [ ]} Tests added or updated
- [ ] No new warnings or errors
```

---

## Step 2 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `branch_base` → default base branch
- `atlassian_domain`, `cloud_id` → Jira coordinates (only when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 3 — Resolve defaults

- If `{{base}}` was not provided, use `{config.branch_base}`.

## Step 4 — Detect the ticket reference

If `{{ticket_id}}` was provided, use it.

If not, run `git branch --show-current` and extract:
- Jira: match `DM-[0-9]+` (case-insensitive), uppercase result → store as `ticket_ref`
- GitHub Issues: match `gh-([0-9]+)`, extract number → store as `ticket_ref` = `gh-{number}`
- none: no ticket ref

## Step 5 — Fetch ticket details

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

## Step 6 — Gather git context

Resolve the base ref (try in order, use the first that exists):
```
git rev-parse --verify origin/{{base}}
git rev-parse --verify {{base}}
```

If neither ref exists:
1. Tell the user: "`origin/{{base}}` was not found."
2. Ask them to paste the commit messages for this PR.
3. Stop until they reply. Use only their pasted messages for commit context in Steps 7–10.

If the base ref exists, compute the merge-base and collect commits:
```
git merge-base HEAD <base-ref>
git log <merge-base>..HEAD --oneline
git diff <merge-base>..HEAD --stat
```

Use only commits after the merge-base. Do not include commits on the base branch itself.

## Step 7 — Write the Summary

1–2 sentences covering:
1. What changed (from commits and ticket summary)
2. Why this approach was chosen (from ticket description, commit bodies, or non-obvious decisions in the diff)

Do not copy-paste commit messages. Synthesise into one or two tight sentences.

## Step 8 — Write the Review focus

1–2 sentences telling the reviewer:
- Where to concentrate their attention (the most important or risky file/change)
- What they can skim or skip (scaffolding, generated files, mechanical changes)

If the PR is small and straightforward, write "Review the full diff — it is small."

## Step 9 — Write the Changes section

Bullet list of meaningful changes from the reviewer's perspective. One bullet per logical concern, not one per commit. Group related commits. Be specific — file names, config keys, function names where useful. Do not list trivial formatting or whitespace changes.

## Step 10 — Write the Validation section

List what was actually tested or verified. Use `[x]` for each item. Draw from:
- Commits that mention testing, running, or verifying
- Test files in the diff
- CI status if mentioned

If no evidence of testing exists in the diff or commits, write a single unchecked item: `[ ] Manual testing required`.

## Step 11 — Write the Notes section

Surface anything a reviewer needs to know that isn't obvious from the diff:
- Non-obvious technical decisions and the reasoning behind them
- Known issues or limitations introduced by this PR
- Follow-up tickets recommended
- Workarounds for environment or tooling constraints

If there is nothing to surface, write `None`.

## Step 12 — Evaluate the checklist

Check each item independently against the diff and commit list:

- **Self-reviewed** — always `[x]`: the PR description itself is evidence of review.
- **Tests added or updated** — `[x]` if the diff includes files matching `*test*`, `*spec*`, `tests/`, `__tests__/`, or test framework imports. Otherwise `[ ]`.
- **No new warnings or errors** — always `[ ]`: cannot be verified from diff alone; leave for the developer.

Output only the filled-in template. No text before or after it.
