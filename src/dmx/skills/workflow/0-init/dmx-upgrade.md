---
name: upgrade
title: Upgrade — Refresh IDE Rules and Migrate the Layout
description: Refresh IDE rule files to the current dmx workflow version, then move a 0.5 repo's ticket files into their job folders. Does not change config or loop state.
---

You are refreshing dmx IDE rule files, then migrating the `.dmx/` layout if the repo still uses the 0.5 one. Ask only the one question in Step 6. Do not touch `.dmx/config.md` or loop state. Do not commit.

Refresh every IDE that already has dmx rule files, plus the invoking IDE.

## Step 1 — Detect the invoking IDE

Call `detect_invoking_ide` with no arguments. Record the returned `ides` list.

## Step 2 — Build the new rule files

Call `setup_ide_rules` with `include_existing` set to true.

If `ides` from step 1 is empty, also pass `ides="cursor"`. Otherwise pass that `ides` list.

The tool adds every IDE that already has dmx rule files. Do not look for those files yourself.

## Step 3 — Skip when the rules are current

If `already_current` is true, tell the developer "dmx rules are already current" and the `workflow_version`. Do not write rule files. Go to Step 6.

## Step 4 — Write the returned files

Write each returned file as the complete file at <workspace_root>/<path>, creating parent directories as needed. Per-rule files (.cursor/rules/*.mdc, .claude/rules/*.md, .agents/rules/*.md) and summary files (.cursor/AGENTS.md, CLAUDE.md, AGENTS.md, .github/copilot-instructions.md) are both complete file contents. A dmx block runs from any line that starts with `<!-- deepmodel:dmx:start` to the next `<!-- deepmodel:dmx:end -->`. The returned summary file already has every old dmx block removed and the new block written once, where the first old block was. If there was no dmx block, the block is appended. Content outside dmx blocks is never changed. Do not splice markers yourself. After writing, open a new chat for the rules to take effect.

If `files` is empty, write nothing.

## Step 5 — Tell the developer

If `previous_version` is set, say "dmx rules updated from {previous_version} to {workflow_version}". Otherwise say "dmx rules updated to {workflow_version}".

List every path you wrote. Tell the developer to open a new chat and commit the rule files. Do not commit them.

If `notes` lists files that could not be read or have no end to their dmx block, tell the developer which files were not changed and that they need to be fixed by hand before re-running `/dmx/upgrade`.

## Step 6 — Migrate the `.dmx/` layout

Since 0.6, each ticket's `spec.md`, `tasks.md` and `activeContext.md` live in `.dmx/jobs/<job>/` instead of the `.dmx/` root.

Call `migrate_dmx_layout` with `dry_run` set to true.

- If `blocked` is set, tell the developer that message and stop. Rule files you wrote in Step 4 stay written.
- If `has_changes` is false and `references` is empty, say "The `.dmx/` layout is already current." Stop.

Otherwise show the plan, one list per field, omitting empty ones:

- `moves` — this branch's ticket files moving into its job folder (`from` → `to`).
- `deletes` — root ticket files from an earlier ticket, removed. Their content stays in git history.
- `kept` — root files left in place because the job folder already has a file of that name. The developer merges them by hand.
- `references` — custom validators, loops, skills or shared-source files that still name the old paths. They break once the files move: the developer updates them to the job folder. A validator can read `spec_path`, `tasks_path` and `active_context_path` from `loop_context`.
- `adds_gitattributes` — adds `gitattributes_line` to `.gitattributes`.
- `open_items` — open learnings or decisions in a root `activeContext.md` that is being deleted.

Ask once: "Apply this migration? (yes/no)". If the answer is not yes, change nothing and stop.

If `open_items` is not empty, first promote each item into the core memory bank files the way `/dmx/update-memory` does, so nothing is lost when the root `activeContext.md` is deleted.

Then call `migrate_dmx_layout` with `dry_run` set to false and list what it moved and deleted. Tell the developer to commit the rule files and the migration together as one PR. Do not commit them.
