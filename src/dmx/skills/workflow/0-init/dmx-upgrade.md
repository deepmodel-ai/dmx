---
name: upgrade
title: Upgrade — Refresh IDE Rules
description: Refresh IDE rule files to the current dmx workflow version. Does not change config, the memory bank, or loop state.
---

You are refreshing dmx IDE rule files. Do not ask questions. Do not touch `.dmx/config.md`, the memory bank, or `.dmx/jobs/`. Do not commit.

Refresh every IDE that already has dmx rule files, plus the invoking IDE.

## Step 1 — Detect the invoking IDE

Call `detect_invoking_ide` with no arguments. Record the returned `ides` list.

## Step 2 — Build the new rule files

Call `setup_ide_rules` with `include_existing` set to true.

If `ides` from step 1 is empty, also pass `ides="cursor"`. Otherwise pass that `ides` list.

The tool adds every IDE that already has dmx rule files. Do not look for those files yourself.

## Step 3 — Stop when the rules are current

If `already_current` is true, tell the developer "dmx rules are already current" and the `workflow_version`. Change nothing. Do not write files. Stop.

## Step 4 — Write the returned files

Write each returned file as the complete file at <workspace_root>/<path>, creating parent directories as needed. Per-rule files (.cursor/rules/*.mdc, .claude/rules/*.md, .agents/rules/*.md) and summary files (.cursor/AGENTS.md, CLAUDE.md, AGENTS.md, .github/copilot-instructions.md) are both complete file contents. A dmx block runs from any line that starts with `<!-- deepmodel:dmx:start` to the next `<!-- deepmodel:dmx:end -->`. The returned summary file already has every old dmx block removed and the new block written once, where the first old block was. If there was no dmx block, the block is appended. Content outside dmx blocks is never changed. Do not splice markers yourself. After writing, open a new chat for the rules to take effect.

If `files` is empty, write nothing.

## Step 5 — Tell the developer

If `previous_version` is set, say "dmx rules updated from {previous_version} to {workflow_version}". Otherwise say "dmx rules updated to {workflow_version}".

List every path you wrote. Tell the developer to open a new chat and commit the rule files. Do not commit them.

If `notes` lists files that could not be read or have no end to their dmx block, tell the developer which files were not changed and that they need to be fixed by hand before re-running `/dmx/upgrade`.
