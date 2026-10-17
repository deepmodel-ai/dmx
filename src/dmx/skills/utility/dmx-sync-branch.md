---
name: sync-branch
title: Sync Branch
description: Rebase (or merge) the latest integration branch onto the current feature or bug branch to keep it up to date. Pushes the result to origin.
arguments:
  - name: strategy
    description: "Integration strategy. Accepted values: rebase (default), merge."
    required: false
---

You are syncing the current branch with the latest integration branch (`branch_base`). Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `branch_base` → integration branch to sync from
- `production_branch` → production branch (when set in config)

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

Guard: if `current_branch` is a protected branch — `master`, `main`, `staging`, `development`, `{config.branch_base}`, or `{config.production_branch}` when set in config — stop: "This command is only for feature or bug branches. Do not sync protected branches with it."

## Step 4 — Check for a clean working tree

Run:
```
git status --short
```

If there are uncommitted changes, stop:
"You have uncommitted changes. Commit or stash them before syncing:
- To commit: run /dmx/commit
- To stash:  run git stash"

## Step 4b — Move this branch's ticket files first

If `.dmx/spec.md`, `.dmx/tasks.md` or `.dmx/activeContext.md` exists, this branch still uses the 0.5 layout. Moving its files into its job folder before integrating keeps git from applying its edits to another ticket's files:

1. Call `migrate_dmx_layout` with `dry_run` set to true. If `blocked` is set, tell the developer and stop.
2. Show `moves`, `deletes` and `references`, and ask once: "Move this branch's ticket files before syncing? (yes/no)". If not yes, stop.
3. Call `migrate_dmx_layout` with `dry_run` set to false, then commit:
```
git add -A .dmx .gitattributes
git commit -m "chore: move ticket files into the job folder"
```

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
{if every conflicting file is a ticket file — .dmx/spec.md, .dmx/tasks.md, .dmx/activeContext.md, or a file under .dmx/jobs/:}
These are dmx ticket files, moved in the 0.6 layout change. Keep this branch's version of each (`git add <file>`); a root file left this way is moved into its job folder on the next loop step.
{if a conflicting file is a memory file — .dmx/*.md or .dmx/components/*/*.md — or another ticket's activeContext.md:}
Both sides edited memory. Merge the two versions by hand: keep every learning once. If both sides folded in the same learning, keep one copy; if both marked the same item (`→ owner, ref`), keep one mark.

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
