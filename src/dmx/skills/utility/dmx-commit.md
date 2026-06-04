---
name: commit
title: Commit Changes
description: Stage changes, refresh the memory bank with light progress updates, and create a Conventional Commits formatted commit message referencing the current Jira ticket. Never auto-pushes.
arguments:
  - name: message
    description: Override the generated commit summary. If omitted, the summary is generated from the staged diff.
    required: false
  - name: type
    description: "Conventional Commits type. Accepted values: feat, fix, refactor, chore, docs, test, style, perf, ci, build. If omitted, inferred from the branch prefix."
    required: false
  - name: scope
    description: Optional scope in parentheses, e.g. auth, api, db. Omit if not applicable.
    required: false
  - name: body
    description: Optional multi-line explanation added after the summary line.
    required: false
  - name: stage_all
    description: "If true (default), run git add . before committing. Set to false to commit only already-staged files."
    required: false
---

You are creating a git commit. Follow every step in order. Never push — committing and pushing are separate decisions.

## Step 1 — Protected branch check

Run:
```
git branch --show-current
```

Protected branches: `master`, `main`, `staging`, `development`.

If the current branch is one of the protected branches:
1. Tell the user which branch they are on.
2. Ask explicitly: "You are on `{branch}`. Committing directly to this branch is usually unintentional. Do you want to proceed?"
3. Stop and wait for their answer. Do not stage or commit until they confirm.

If they decline, stop. If they confirm, or the branch is not protected, continue.

## Step 2 — Check for changes

Run:
```
git status --short
```

- If `{{stage_all}}` is not `false` and there are unstaged or untracked changes, proceed to Step 3.
- If `{{stage_all}}` is `false` and there are no staged changes, stop: "Nothing is staged. Either stage your changes with `git add` or re-run without `stage_all: false`."
- If the working tree is completely clean, stop: "Nothing to commit — working tree is clean."

## Step 3 — Light memory bank update

Before staging, refresh the memory bank with progress from this commit.

Extract the ticket ref from the branch name (Jira: `[A-Z]+-[0-9]+`; GitHub Issues: `gh-[0-9]+`) or from `.dmx/activeContext.md`.

Read:
- `.dmx/activeContext.md`
- `.dmx/tickets/active/{ticket_ref}/tasks.md` (if that folder exists)

Run `git diff` and `git status --short` to understand unstaged changes.

**Always update `activeContext.md`:**
- Keep `## Active Ticket` as-is unless the branch name reveals a different ticket
- Update `## Current Focus` with the current phase and next unchecked task from `tasks.md`, or a one-line summary of what this commit covers
- Do not clear the active ticket here

**Update other core files only when the diff clearly warrants it:**
- `techContext.md` — new dependencies, env vars, or tooling (e.g. changes to `pyproject.toml`, `package.json`, `requirements.txt`, `.env.example`)
- `systemPatterns.md` — new architectural patterns or design decisions visible in the diff
- `productContext.md` — user-facing behaviour changes visible in the diff

Rules:
- Append to the relevant section; do not rewrite whole files
- Never delete existing content
- Skip a file entirely if nothing new belongs there
- Do not extract ticket-specific implementation details into core files

## Step 4 — Stage files

If `{{stage_all}}` is not `false`, run:
```
git add .
```

## Step 5 — Inspect the diff

Run in parallel:
```
git diff --staged --stat
git diff --staged
```

Review all hunks. Understand what changed, why it likely changed, and whether the changes form one logical unit.

If the diff clearly mixes unrelated concerns (e.g. a bug fix and an unrelated refactor in different modules), say so:
"This diff appears to mix unrelated changes: {describe the two concerns}. Consider splitting into separate commits for a cleaner history."

Then ask: "Do you want to proceed with a single commit, or stop to split the changes?"

Stop and wait for their answer. Proceed only if they confirm a single commit.

## Step 6 — Detect the ticket ID

Extract the ticket ID from the branch name:
- Jira: match `[A-Z]+-[0-9]+` (case-insensitive), uppercase result (e.g. `dm-1662` → `DM-1662`)
- None found: omit the `Closes` footer line

## Step 7 — Resolve the commit type

If `{{type}}` was provided, use it.

If not, infer from the branch name prefix:
- `feature-` → `feat`
- `bug-` → `fix`
- `hotfix-` → `fix`
- `chore-` → `chore`
- `docs-` → `docs`
- Any other prefix → `chore`

## Step 8 — Build the commit message

If `{{message}}` was provided, use it as the summary line (≤ 72 characters, lowercase first letter, no trailing period).

If `{{message}}` was not provided, write the full message from the diff:

**Summary line** — imperative mood, lowercase first letter, ≤ 72 characters, no trailing period. Describes what this commit does.

**Body — bullet list** — one line per meaningful change, starting with `- `. Be specific: name files, behaviours, or APIs affected. Group related edits. Do not copy-paste diff lines.

**Body — summary paragraph** — 1–3 sentences explaining intent and impact. What problem does this solve, or what does it enable?

**Footer** — `Closes {ticket_id}` if a ticket was found in Step 6.

Assemble using this structure:
```
{type}[({scope})]: {summary}

- first meaningful change
- second meaningful change

One short paragraph explaining intent and impact.

[Closes {ticket_id}]
```

Rules:
- Include `({scope})` only if `{{scope}}` was provided.
- Always include the bullet list and paragraph when generating automatically (no `{{message}}` argument).
- There must be a blank line between the summary, bullet list, paragraph, and footer.
- Do not include tool names or git command names inside the message text.
- When `ai_attribution` is `strip` (default): never include `Co-authored-by:` or any AI attribution trailers in the message text.

Example:
```
feat(auth): add refresh token rotation

- validate refresh tokens before issuing new access tokens
- persist rotated refresh tokens in the session store
- return 401 when refresh token is expired or revoked

Improves session security by limiting replay of stolen refresh tokens.
Keeps access tokens short-lived without requiring full re-authentication.

Closes DM-1662
```

## Step 9 — Commit

Run:
```
git commit -m "{full commit message from Step 8}"
```

For multi-line messages, use a heredoc or properly escaped newlines to preserve blank lines between sections.

After committing, read `ai_attribution` from the project config (default: `strip` if not set).

If `ai_attribution` is `strip` (or not set):
```
git log -1 --format="%B"
```
If the commit message contains any `Co-authored-by:` trailer (regardless of author), amend it out immediately:
```
git commit --amend -m "{full commit message from Step 8 with all Co-authored-by lines removed}"
```

If `ai_attribution` is `keep`, leave the commit as-is.

## Step 10 — Return the result

```
Committed: {short SHA}
{full commit message}

Next:
  - Push:                git push origin HEAD
  - Continue building:   /dmx/implement-next-task or /dmx/implement-next-phase
  - Validate:            /dmx/validate  (when all phases are complete)
  - Open a PR:           /dmx/create-pr  (after validation passes)
```
