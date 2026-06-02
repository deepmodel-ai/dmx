---
name: implement-next-phase
title: Implement Next Phase
description: Execute every task in the next unchecked phase of the current ticket's tasks.md, checking each off as it completes. Stops after the phase and waits for review before proceeding further.
arguments:
  - name: ticket_id
    description: Jira ticket ID, e.g. DM-1667. Auto-detected from activeContext.md if omitted.
    required: false
---

You are implementing the next phase of an active ticket. Follow every step in order. You execute the whole phase — not just a single task — and then stop. Never continue to the next phase automatically.

## Step 1 — Resolve the active ticket

If `{{ticket_id}}` was provided, use it.

If not, read `.dmx/activeContext.md` and extract the ticket ID from the `## Active Ticket` section.

If no active ticket is found, stop and tell the user: "No active ticket found. Provide `ticket_id` or run `/dmx/create-ticket` first."

## Step 2 — Read spec.md and tasks.md

Read both:
- `.dmx/tickets/active/{ticket_id}/spec.md` — for context and technical approach
- `.dmx/tickets/active/{ticket_id}/tasks.md` — for the task list

If tasks.md does not exist, stop and tell the user: "tasks.md not found for {ticket_id}. Run `/dmx/plan` to generate the task plan first."

## Step 3 — Identify the next phase

Scan tasks.md top-to-bottom and find the first phase that contains at least one unchecked task (`- [ ]`).

If all tasks across all phases are checked, stop and output:
```
All phases complete for {ticket_id}.

Next:
  - Commit any remaining changes with /dmx/commit.
  - Run /dmx/validate to run the pre-PR quality gate.
  - Once validated, run /dmx/create-pr to open the pull request.
```

Store the phase name and its full task list.

## Step 4 — Announce the phase

Before doing any work, output:
```
## Implementing {Phase Name}

{N} tasks in this phase:
{list all tasks in the phase, unchecked}
```

## Step 5 — Execute each task in sequence

For each unchecked task in the phase, in order:

1. **Announce the task:** Output `### Task: {task text}`
2. **Implement it:** Make the code change, create the file, write the migration, etc. Apply the coding style from the system prompt (functional-first, immutable data, pure functions, type annotations).
3. **Check it off:** Update tasks.md — change `- [ ]` to `- [x]` for this task.
4. **Briefly report:** 1–2 sentences on what was done. No lengthy explanations.
5. Move to the next task.

Do not skip tasks. Do not reorder tasks. If a task is already checked, skip it silently.

## Step 6 — Phase complete summary

After all tasks in the phase are done, output:

```
## Phase Complete: {Phase Name}

{Bullet summary of what was built — one bullet per task, written as a change log entry}

---
Next:
  - Review the changes, then run /dmx/commit to save your progress.
  - Run /dmx/implement-next-phase for the next phase.
  - Run /dmx/implement-next-task for finer control over the next phase.
```

## Guards

- Stop at the end of the phase. Do not start the next phase even if the user says "continue" — they must explicitly re-run this prompt.
- If a task is blocked (e.g. depends on a file that doesn't exist, or requires a decision not made in the spec), stop immediately. Report: "Blocked on task: {task text}. Reason: {reason}. Update spec.md or clarify the approach before continuing."
- If implementing a task would require changing the scope defined in spec.md, stop and flag it rather than silently expanding scope.
- Never delete or reorder tasks in tasks.md. Only change `[ ]` to `[x]`.
