---
name: update-shared-sources
title: Update Shared Sources
description: Lock the org-wide shared sources declared in .dmx/shared-sources.yaml in .dmx/shared-sources.lock, then commit and push the lock. Run when shared-sources.yaml changes (a new source, a bumped ref) or to move a source to the commit its ref points to now.
arguments:
  - name: sources
    description: "Comma-separated source names to look up again even if their lock entry is current."
    required: false
  - name: all
    description: "Set to true to look up every declared source again."
    required: false
---

You are updating this repo's declared shared sources (loops/skills/validators an organization maintains centrally). The repo commits only `.dmx/shared-sources.lock`, the commit and content hash of each source; dmx fetches the content into the git-ignored `.dmx/.cache/` when it needs it. Follow every step in order.

## Step 1 — Load configuration and protected branch check

The project configuration is injected into your context as a rule. Extract `branch_base`. If not available in context, fall back to reading `.dmx/config.md`.

Run:
```
git branch --show-current
```

If the current branch is `{config.branch_base}`, stop:
```
Cannot run /dmx/update-shared-sources from `{branch_base}` — it commits
.dmx/shared-sources.lock, and every other write path in dmx requires that go
through a reviewed PR rather than landing on `{branch_base}` directly.

Create a branch first, e.g.:
  git checkout -b chore/update-shared-sources

Then run /dmx/update-shared-sources again.
```

Do not proceed to Step 2 in this case.

## Step 2 — Check shared-sources.yaml exists

If `.dmx/shared-sources.yaml` does not exist, stop: "No `.dmx/shared-sources.yaml` found — nothing to update. Add a `shared_sources` entry first if you want to pull in an org-wide source."

## Step 3 — Update the lock

Call `update_shared_sources` on `user-dmx`:
- If `{{sources}}` was provided, pass it as `names`, split on commas, trimmed.
- If `{{all}}` is `true`, pass `update_all` set to true.
- Otherwise pass neither. Only sources that are new or whose address changed in `.dmx/shared-sources.yaml` are looked up again; every other source keeps its locked commit, so a floating ref such as `?ref=main` does not move by surprise.

The tool looks up each of those refs, fetches the tree, checks its shape, hashes it, and writes `.dmx/shared-sources.lock`. It also checks for same-name collisions across the app repo's own `.dmx/loops/`/`.dmx/skills/`/`validators/` and every declared source, in precedence order. It does not commit anything — that is your job in Step 5.

## Step 4 — Handle the result

**If the response is a single top-level error, not a per-source list** (e.g. "Error reading shared sources: ..."): the config or the lock itself is invalid — nothing was attempted. Show the error to the user verbatim and stop. Common causes: bad YAML, a duplicate or invalid `name` (must be a plain slug — letters, digits, `_`, `-`), or an invalid `subdir`/address (missing `?ref=`, a `subdir` starting with `/` or containing a `..` segment).

**If the response says "nothing changed":** stop: "Already up to date — every source is at its locked commit." This is a normal, successful outcome.

**If some sources failed** (`❌` lines): show the user exactly which ones and why, using the tool's own per-source messages (auth failure, missing ref, a wrong-shaped source, or malformed entries under a source's `skills/` directory). A failed source keeps its previous lock entry. Ask whether to proceed and commit the sources that *did* update, or stop and fix the failing entry first. Do not silently commit a partial update without asking.

**If the response includes a "Collisions detected" section:** show every warning to the user verbatim. These are not errors.

## Step 5 — Commit and push

Run:
```
git add .dmx/shared-sources.lock .dmx/shared-sources.yaml
git commit -m "chore: update shared sources"
git push -u origin HEAD
```

## Step 6 — Return the result

```
Updated {N} shared source(s):
{per-source results from Step 3/4}

{if any collisions were reported:}
Collisions detected — review before merging:
{each collision warning, verbatim}

Committed and pushed: {short SHA}

Summary for the PR description:
{the "Summary for the PR description" block from the tool, verbatim}

Next:
  - Open a PR against {config.branch_base} with that summary. Loops and validators marked "code to review" run as code in every loop; review them like any other dependency bump.
```

## Guards

- Never run Step 3 from `{config.branch_base}`.
- Never commit a partial update (some sources failed) without telling the user which ones failed and why, and getting their confirmation.
- Never edit `.dmx/shared-sources.lock` by hand, and never commit `.dmx/.cache/`.
