---
name: sync
title: Sync Shared Sources
description: Vendor every org-wide shared source declared in .dmx/shared-sources.yaml into .dmx/vendor/, then commit and push the result. Run whenever shared-sources.yaml changes (a new source, a bumped ref) or on a fresh clone before relying on org-wide loops/skills/validators.
---

You are syncing this repo's declared shared sources (loops/skills/validators an organization maintains centrally and vendors into every consuming repo). Follow every step in order.

## Step 1 — Load configuration and protected branch check

The project configuration is injected into your context as a rule. Extract `branch_base`. If not available in context, fall back to reading `.dmx/config.md`.

Run:
```
git branch --show-current
```

If the current branch is `{config.branch_base}`, stop:
```
Cannot run /dmx/sync from `{branch_base}` — it commits vendored files directly, and every
other write path in dmx requires that go through a reviewed PR rather than landing on
`{branch_base}` directly.

Create a branch first, e.g.:
  git checkout -b chore/sync-shared-sources

Then run /dmx/sync again.
```

Do not proceed to Step 2 in this case.

## Step 2 — Check shared-sources.yaml exists

If `.dmx/shared-sources.yaml` does not exist, stop: "No `.dmx/shared-sources.yaml` found — nothing to sync. Add a `shared_sources` entry first if you want to pull in an org-wide source."

## Step 3 — Sync

Call `sync_shared_sources` on `user-dmx`. It reads `.dmx/shared-sources.yaml`, clones each declared source at its pinned `ref`, and vendors it into `.dmx/vendor/{name}/` along with a `.lock.json` recording the resolved commit SHA. It does not commit anything — that is your job in the next step.

## Step 4 — Handle the result

**If every source succeeded** (no `❌` lines in the tool's response): proceed to Step 5.

**If some sources failed:** show the user exactly which ones and why, using the tool's own per-source messages (auth failure, missing ref, or a wrong-shaped source are the three distinguishable causes it reports). Ask whether to proceed and commit the sources that *did* succeed, or stop entirely and fix the failing entry in `.dmx/shared-sources.yaml` first. Do not silently commit a partial sync without asking.

**If every source failed:** stop. There is nothing to commit.

## Step 5 — Commit and push

Run:
```
git add .dmx/vendor/ .dmx/shared-sources.yaml
git status --short
```

If nothing is staged (e.g. re-running against an already-synced, unchanged ref), stop: "Already up to date — nothing changed since the last sync."

Otherwise commit:
```
git commit -m "chore: sync shared sources"
```

Then push:
```
git push -u origin HEAD
```

## Step 6 — Return the result

```
Synced {N} shared source(s):
{per-source summary from Step 3/4 — success with resolved SHA, or failure reason}

Committed and pushed: {short SHA}

Next:
  - Open a PR against {config.branch_base} for review, same as any other dependency bump.
  - If a source's files collide with something already in this repo (an app-repo override,
    or another shared source), that's not detected yet — collision detection lands in a
    follow-up. Check `.dmx/loops/`, `.dmx/skills/`, `validators/`, and the other entries in
    `.dmx/vendor/` by hand for now if you suspect an overlap.
```

## Guards

- Never run Step 3 from `{config.branch_base}` — Step 1's guard exists specifically because this skill produces a commit, and every other dmx write path requires that go through a PR.
- Never commit a partial sync (some sources failed) without explicitly telling the user which ones failed and why, and getting their confirmation to proceed anyway.
- Never treat "nothing staged in Step 5" as an error — it means the sync was already up to date, which is a normal, successful outcome.
