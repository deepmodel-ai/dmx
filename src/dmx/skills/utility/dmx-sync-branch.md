---
name: sync-branch
title: Sync Branch
description: Rebase (or merge) the latest base branch onto the current feature or bug branch to keep it up to date. Pushes the result to origin.
arguments:
  - name: strategy
    description: "Integration strategy. Accepted values: rebase (default), merge."
    required: false
---

You are syncing the current branch with the latest base branch. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `branch_base` → the base branch to sync from

If not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Resolve defaults

- If `{{strategy}}` was not provided, use `rebase`.
- Accepted values: `merge`, `rebase` only. Anything else — stop and ask the user to correct it.

## Step 3 — Identify the current branch

Run:
```
git branch --show-current
```

Store as `current_branch`.

Guard: if `current_branch` is `{config.branch_base}`, `master`, or `development`, stop: "This command is only for feature or bug branches. Do not sync protected branches with it."

## Step 4 — Check for a clean working tree

Run:
```
git status --short
```

If there are uncommitted changes, stop:
"You have uncommitted changes. Commit or stash them before syncing:
- To commit: run /dmx/commit
- To stash:  run git stash"

## Step 5 — Fetch latest from origin

Run:
```
git fetch origin
```

## Step 6 — Integrate upstream changes

**If `{{strategy}}` is `merge`:**
```
git merge origin/{config.branch_base}
```

**If `{{strategy}}` is `rebase`:**
```
git rebase origin/{config.branch_base}
```

**If conflicts occur:** Stop. Do not continue. Output:
```
Conflicts detected. Resolve the following files manually:
{list of conflicting files}

After resolving:
- For merge:  git add . && git merge --continue
- For rebase: git add . && git rebase --continue

Then push: git push origin HEAD
```

## Step 7 — Push the updated branch

Run:
```
git push origin HEAD
```

If push is rejected, do not force-push. Tell the user: "Push rejected — the remote branch has commits not present locally. Run `git pull --rebase origin {current_branch}` to reconcile, then push again."

## Step 8 — Return the result

Run `git log origin/{config.branch_base}..HEAD --oneline` to count commits ahead.

Output:
```
Synced {current_branch} with {config.branch_base} ({strategy}).
{N} commit(s) ahead of {config.branch_base}. Up to date.
```
