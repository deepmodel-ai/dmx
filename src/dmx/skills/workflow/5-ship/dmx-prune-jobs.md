---
name: prune-jobs
title: Remove Finished Jobs
description: 'Remove finished job folders from .dmx/jobs/ in one commit, "chore: remove N finished jobs". Runs before create-pr in the release loop. Keeps the current ticket, unfinished jobs, and jobs with open learnings or decisions.'
arguments:
  - name: older_than
    description: "Minimum age, as a number of days such as 7d. Defaults to 30d."
    required: false
---

You are removing finished job folders from `.dmx/jobs/` before the PR opens. Each merged ticket leaves its folder there; this keeps the folder from growing without end. Git history keeps the files. Follow every step in order.

A job is removed when all its loop runs finished, its folder is on `branch_base` (its PR merged), and its newest run finished more than `older_than` ago. dmx never removes the current ticket, a job with an unfinished run, a job whose `activeContext.md` still has open learnings or decisions not yet marked as folded (`→ owner, ref`), or a job with uncommitted changes.

## Step 1 — Remove finished jobs

Call `prune_jobs` on `user-dmx`. If `{{older_than}}` was provided, pass its number of days as `older_than_days` (`7d` → `7`). Otherwise pass nothing; the default is 30 days.

The tool decides which jobs go, removes them with `git rm`, and commits only those removals as `chore: remove N finished jobs`. Other staged changes stay out of that commit. Do not remove, restore, or commit job folders yourself, and do not amend or squash that commit.

## Step 2 — Handle the result

- **`blocked` is set** (a protected branch, a detached HEAD, `branch_base` not found, or a git error): nothing was removed. Show the reason in one line and continue. This step never stops the loop.
- **`removes` is empty:** nothing was old enough to remove. Continue.
- **`removes` lists jobs:** they were removed in commit `commit`, titled `commit_message`. `create-pr` pushes it, and the drafted PR description gets one line telling reviewers they can skip it.

Do not list every kept job. Mention a kept job only when its reason is open learnings or decisions: they were never promoted, so the job stays until someone moves them into the core memory files or clears them.

## Step 3 — Return the result

```
{if jobs were removed:} Removed {N} finished job(s) in {commit}: {job ids}
{if nothing was removed:} No finished jobs to remove.
{if blocked:} Skipped removing finished jobs: {blocked}
{for each job kept for open learnings or decisions:} Kept {job}: open learnings or decisions in its activeContext.md
```

To keep job history instead, give the repo its own `.dmx/loops/release.yaml` without the `prune-jobs` step. To remove jobs by hand, run `dmx prune-jobs --older-than 7d`.
