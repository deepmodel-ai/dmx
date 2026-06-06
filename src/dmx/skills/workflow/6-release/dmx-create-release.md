---
name: create-release
title: Create GitHub Release
description: Tag the production branch and publish a GitHub release with the drafted release notes. Reads from .dmx/releases/{version}.md if already drafted. Run only after the release-merge PR has been merged into the production branch (or directly on trunk repos where integration and production are the same branch).
arguments:
  - name: version
    description: Version tag to create, e.g. v0.14.0. Must start with "v" followed by semver.
    required: true
  - name: prerelease
    description: "Mark this release as a pre-release on GitHub. Defaults to false."
    required: false
---

You are creating the official GitHub release. Follow every step in order. This action is irreversible — verify with the user before tagging.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `branch_base` → integration branch
- `production_branch` → production branch (tag and release target)
- `owner`, `repo` → GitHub coordinates

If not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

If `production_branch` is set in config, use it.

If missing from config:
```
git branch -a
```
Treat a name as present if it appears as a local branch or as `origin/{name}` / `remotes/origin/{name}`.
- If only `master` exists → `production_branch` = `master`
- If only `main` exists → `production_branch` = `main`
- If **both** `master` and `main` exist → stop: "Both master and main exist. Set `production_branch` in `.dmx/config.md` or re-run `/dmx/init`."
- If neither exists → stop: "`production_branch` not set in config. Run /dmx/init to configure it."

## Step 2 — Validate the version format

The version must match `v[0-9]+\.[0-9]+\.[0-9]+` (e.g. `v0.14.0`) or `v[0-9]+\.[0-9]+\.[0-9]+-[a-z]+` for pre-releases (e.g. `v0.14.0-rc1`).

If it does not match, stop: "Version `{{version}}` does not follow the expected format. Use `v{major}.{minor}.{patch}` (e.g. `v0.14.0`)."

## Step 3 — Resolve defaults

If `{{prerelease}}` was not provided, use `false`.

## Step 4 — Verify production has absorbed integration

Run:
```
git fetch origin
git log origin/{config.production_branch}..origin/{config.branch_base} --oneline
```

If this returns commits, warn: "{config.branch_base} is still ahead of {config.production_branch} — the release-merge PR may not have been merged yet. Are you sure you want to proceed?"

Ask the user to confirm with Y/N before continuing. If they say N or do not confirm, stop.

## Step 5 — Check the tag does not already exist

Run:
```
git tag -l {{version}}
git ls-remote --tags origin refs/tags/{{version}}
```

If the tag already exists locally or on origin, stop: "Tag `{{version}}` already exists. Delete it first or choose a different version."

## Step 6 — Load release notes

Check if `.dmx/releases/{{version}}.md` exists.

If it does, read it and use its content as the release body.

If it does not, invoke the `dmx-draft-release-note` prompt with `version: {{version}}`. It will generate and save the file. Use its output as the release body.

## Step 7 — Confirm with the user

Output:
```
About to create release {{version}}:

  Tag:        {{version}} on origin/{config.production_branch}
  Prerelease: {prerelease}
  Notes:      .dmx/releases/{{version}}.md

Preview (first 10 lines):
{first 10 lines of the release notes}

Type CONFIRM to proceed, or anything else to abort.
```

Wait for explicit `CONFIRM` (case-insensitive). Any other response — stop immediately.

## Step 8 — Create and push the tag

Run:
```
git checkout {config.production_branch}
git pull origin {config.production_branch}
git tag -a {{version}} -m "Release {{version}}"
git push origin {{version}}
```

If `git push origin {{version}}` fails, stop and report the error. Do not proceed to create the GitHub release.

## Step 9 — Create the GitHub release

Run:
```
gh release create {{version}} \
  --title "{{version}}" \
  --notes-file .dmx/releases/{{version}}.md \
  {if prerelease: --prerelease} \
  --target {config.production_branch}
```

If `gh` is not available, stop: "The `gh` CLI is required for this step. Install it from https://cli.github.com and authenticate with `gh auth login`."

## Step 10 — Return the result

```
Release created: {GitHub release URL}
Tag: {{version}} on {config.production_branch}

Post-release checklist:
  - Verify the deployment pipeline has triggered (check GitHub Actions).
  - If this was a hotfix, confirm back-merge PRs ({config.production_branch} → {config.branch_base}) have been merged.
```

## Guards

- Never tag any branch other than `{config.production_branch}`.
- Never delete or move an existing tag.
- Never proceed past Step 7 without explicit `CONFIRM` from the user.
