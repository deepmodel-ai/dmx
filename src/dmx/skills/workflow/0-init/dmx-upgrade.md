---
name: upgrade
title: Upgrade — Refresh IDE Rules and Migrate the Layout
description: Refresh IDE rule files to the current dmx workflow version, then move a 0.5 repo's ticket files into their job folders. Does not change config or loop state.
---

You are refreshing dmx IDE rule files, then migrating the `.dmx/` layout if the repo still uses the 0.5 one. The upgrade goes through a PR, so it is written and committed on its own branch. Ask only the questions in Steps 4, 7 and 8, plus which IDE the developer uses when dmx can't tell (Step 2). Do not touch `.dmx/config.md` or loop state.

Refresh every IDE that already has dmx rule files, plus the invoking IDE.

## Step 1 — Detect the invoking IDE

Call `detect_invoking_ide` with no arguments. Record the returned `ides` list.

## Step 2 — Build the new rule files

Call `setup_ide_rules` with `include_existing` set to true.

If `ides` from step 1 is empty, don't pass `ides`: the tool uses the IDEs that already have dmx rule files. If it then returns no files because it can't determine any IDE, ask the developer which IDE they use with the same question as `/dmx/init` Step 6, and call it again with their answer. Never pass `cursor` as a guess: it would add Cursor's files to a repo that doesn't use Cursor. Otherwise pass that `ides` list.

The tool adds every IDE that already has dmx rule files. Do not look for those files yourself.

Don't write the files yet.

## Step 3 — Stop when there is nothing to do

Call `migrate_dmx_layout` with `dry_run` set to true. There is a migration to apply when `blocked` is set or `has_changes` is true.

If `already_current` is true, there is no migration, and `references` is empty, say "dmx rules are already current ({workflow_version}) and the `.dmx/` layout is current." Stop. No branch is created.

## Step 4 — Get onto the upgrade branch

Read `branch_base` and `production_branch` from `.dmx/config.md`, and run `git branch --show-current`. The upgrade branch is `chore-dmx-upgrade-{workflow_version}`.

- **No branch (detached HEAD):** stop: "Check out a branch, then run `/dmx/upgrade` again."
- **On `branch_base` or `production_branch`:** run `git status --porcelain`. If it lists anything, stop: "You have uncommitted changes on {branch}. The upgrade goes on its own branch and would take them with it. Commit or stash them, then run `/dmx/upgrade` again." Otherwise switch without asking, as below, and tell the developer: "The upgrade goes through a PR, so I created `chore-dmx-upgrade-{workflow_version}` from `origin/{branch}`."
- **On any other branch:** ask:
  ```
  You're on {branch}. Where should the dmx upgrade go?

    1. On {branch}, in this branch's PR
    2. On its own branch, chore-dmx-upgrade-{workflow_version}, from {branch_base}

  Enter 1 or 2:
  ```
  Wait for the answer. For 1, stay here. For 2, check `git status --porcelain` as above, then switch.

To switch: run `git fetch origin {base}`, where `{base}` is the branch you are on, or `branch_base` for option 2. If `chore-dmx-upgrade-{workflow_version}` exists locally or on `origin`, run `git switch chore-dmx-upgrade-{workflow_version}`. Otherwise run `git switch -c chore-dmx-upgrade-{workflow_version} origin/{base}`.

If you switched branches, call `setup_ide_rules` again with the same arguments as Step 2 and use its result from here on, since the files on the new branch can differ.

## Step 5 — Write the returned files

Write each returned file as the complete file at <workspace_root>/<path>, creating parent directories as needed. Per-rule files (.cursor/rules/*.mdc, .claude/rules/*.md, .agents/rules/*.md) and summary files (.cursor/AGENTS.md, CLAUDE.md, AGENTS.md, .github/copilot-instructions.md) are both complete file contents. A dmx block runs from any line that starts with `<!-- deepmodel:dmx:start` to the next `<!-- deepmodel:dmx:end -->`. The returned summary file already has every old dmx block removed and the new block written once, where the first old block was. If there was no dmx block, the block is appended. Content outside dmx blocks is never changed. Do not splice markers yourself. After writing, open a new chat for the rules to take effect.

If `files` is empty or `already_current` is true, write nothing.

## Step 6 — Tell the developer

If `previous_version` is set, say "dmx rules updated from {previous_version} to {workflow_version}". Otherwise say "dmx rules updated to {workflow_version}".

List every path you wrote.

If `notes` lists files that could not be read or have no end to their dmx block, tell the developer which files were not changed and that they need to be fixed by hand before re-running `/dmx/upgrade`.

## Step 7 — Migrate the `.dmx/` layout

Since 0.6, each ticket's `spec.md`, `tasks.md` and `activeContext.md` live in `.dmx/jobs/<job>/` instead of the `.dmx/` root, and shared sources are locked in `.dmx/shared-sources.lock` and fetched into the git-ignored `.dmx/.cache/` instead of being copied into `.dmx/vendor/`.

Call `migrate_dmx_layout` with `dry_run` set to true.

- If `blocked` is set, tell the developer that message and stop. Rule files you wrote in Step 5 stay written.
- If `has_changes` is false and `references` is empty, say "The `.dmx/` layout is already current." Go to Step 8.

Otherwise show the plan, one list per field, omitting empty ones:

- `moves` — this branch's ticket files moving into its job folder (`from` → `to`).
- `deletes` — root ticket files from an earlier ticket, removed. Their content stays in git history.
- `kept` — root files left in place because the job folder already has a file of that name. The developer merges them by hand.
- `references` — custom validators, loops, skills or shared-source files that still name the old paths. They break once the files move: the developer updates them to the job folder. A validator can read `spec_path`, `tasks_path` and `active_context_path` from `loop_context`.
- `adds_gitattributes` — adds `gitattributes_line` to `.gitattributes`.
- `open_items` — open learnings or decisions in a root `activeContext.md` that is being deleted.
- `locks` — shared sources vendored in `.dmx/vendor/`, locked at the commit they were vendored at. Applying fetches each one, so this machine needs read access to those repos.
- `removes_vendor` — `.dmx/vendor/` is removed once every vendored source is locked.
- `adds_gitignore` — adds `gitignore_line` to `.gitignore`.
- `needs_update` — declared shared sources with neither a lock entry nor a vendored copy. Run `/dmx/update-shared-sources` after the migration.

Ask once: "Apply this migration? (yes/no)". If the answer is not yes, change nothing and go to Step 8.

If `open_items` is not empty, first promote each item into the core memory bank files the way `/dmx/update-memory` does, so nothing is lost when the root `activeContext.md` is deleted.

Then call `migrate_dmx_layout` with `dry_run` set to false and list what it moved, deleted and locked.

If `lock_failures` is not empty, show each one verbatim: those sources could not be fetched, so `.dmx/vendor/` was kept and they still resolve from it. Fix access and run `/dmx/upgrade` again.

If the repo has shared sources, tell the developer that CI which runs shared validators now needs read access to those repos: give it a read token and run `dmx fetch-shared-sources` before the tests.

## Step 8 — Commit, then offer the PR

Stage only what this upgrade changed: the rule files you wrote, plus, if the migration ran, `.dmx/`, `.gitattributes` and `.gitignore`. Commit them in one commit, `chore: upgrade dmx rules to {workflow_version}`. Don't stage anything else. Follow `ai_attribution` in `.dmx/config.md` as `/dmx/commit` does: unless it is `keep`, the message has no `Co-authored-by:` or other AI attribution lines. If nothing changed (the rules were current and the migration was declined), say so and stop.

If you stayed on the developer's branch (option 1), also add a line to the ticket's `spec.md` Scope under a "dmx setup" heading: "dmx rules upgraded to {workflow_version}, at the developer's request." Don't offer a PR: the upgrade goes in this branch's PR.

On `chore-dmx-upgrade-{workflow_version}`, ask once: "Push `chore-dmx-upgrade-{workflow_version}` and open a PR into `{base}`? (yes/no)". For yes, push with `git push -u origin HEAD` and open the PR with the GitHub access in `.dmx/config.md`. Title it `chore: upgrade dmx rules to {workflow_version}`, and list in the body what changed. Do not merge it. For no, say the commit is on the branch, ready to push.

Finally, tell the developer to open a new chat so the new rules take effect, and that teammates get them when the PR merges.
