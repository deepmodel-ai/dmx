---
name: check
title: Check Setup
description: Check the dmx setup — components, dependencies, paths, test commands, shared-source locks, CODEOWNERS, stray .dmx/ folders, waiting learnings — and show what this branch will load, check and run. Never changes a file.
---

You are checking this repo's dmx setup. Do not change any file.

## Step 1 — Run the check

Call `check_setup` on `user-dmx`.

## Step 2 — Show the report

Show the report verbatim, in a code block. Do not summarise or reorder it.

Then, if it has any `✗` lines, say: "{N} problem(s) to fix. Each line ends with the command or edit that fixes it." If it has only `!` or `i` lines, say: "No errors. The `!` lines are worth a look." If it has neither, say: "The setup is clean."

Do not run the fixes yourself unless the developer asks. Each fix names its own command, such as `/dmx/update-shared-sources ledger` or `/dmx/add-component services/fraud`.

The same report is available on the command line as `dmx check`, which exits 1 when there is an error, so CI can run it.
