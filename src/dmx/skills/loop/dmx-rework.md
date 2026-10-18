---
name: rework
title: Rework Failed Checks
description: Turn a paused loop's failed required checks into rework tasks, implement them, then re-run the loop.
arguments:
  - name: loop
    description: "Loop that does the fixing. Defaults to dev."
    required: false
  - name: max_rounds
    description: "Rework rounds to allow. Defaults to 2."
    required: false
---

Call the `rework_loop` MCP tool now.

- Loop: `{{loop}}`. If this is empty, leave `loop` out (it defaults to `dev`).
- Rounds: `{{max_rounds}}`. If this is empty, leave `max_rounds` out (it defaults to 2). Otherwise pass it as a number.

Report the result to the user exactly as returned by the tool, then follow any instructions it contains. If it lists rework tasks and says to wait for approval, stop: the developer reviews the tasks in `tasks.md`, then runs `/loop-continue`.
