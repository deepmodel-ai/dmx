---
name: implement-next-task
title: Implement Next Task
description: Execute the single next unchecked task in tasks.md and stop. Use this for fine-grained control when tasks are large or risky and you want to review each one individually before proceeding.
arguments:
  - name: ticket_id
    description: Jira ticket ID, e.g. DM-1667. Auto-detected from activeContext.md if omitted.
    required: false
---

You are implementing one task from the active ticket's plan. Execute exactly one task and stop. Do not move on without being asked again.

## Step 1 — Resolve the active ticket

If `{{ticket_id}}` was provided, use it.

If not, read `.dmx/activeContext.md` and extract the ticket ID from the `## Active Ticket` section.

If no active ticket is found, stop and tell the user: "No active ticket found. Provide `ticket_id` or run `/dmx/create-ticket` first."

## Step 2 — Read spec.md and tasks.md

Read both:
- `.dmx/tickets/active/{ticket_id}/spec.md` — for context and technical approach
- `.dmx/tickets/active/{ticket_id}/tasks.md` — for the task list

If tasks.md does not exist, stop and tell the user: "tasks.md not found for {ticket_id}. Run `/dmx/plan` to generate the task plan first."

## Step 3 — Find the next unchecked task

Scan tasks.md top-to-bottom, phase by phase, and find the first unchecked task (`- [ ]`).

Note which phase it belongs to.

If all tasks are checked, stop and output:
```
All tasks complete for {ticket_id}.

Next:
  - Commit any remaining changes with /dmx/commit.
  - Run /dmx/validate to run the pre-PR quality gate.
  - Once validated, run /dmx/create-pr to open the pull request.
```

## Step 4 — Announce the task

Output:
```
## Task ({Phase Name}): {task text}
```

## Step 5 — Implement the task

Make the code change, create the file, write the migration, or perform whatever the task specifies. Apply the coding style from the system prompt (functional-first, immutable data, pure functions, type annotations).

## Step 6 — Check it off

Update tasks.md — change `- [ ]` to `- [x]` for this task only.

## Step 7 — Report and pause

Output:
```
Done: {task text}

{1–3 sentences on what was changed and any notable decision made.}

---
{N} tasks remaining in {Phase Name}. {M} phases remaining overall.

Next:
  - Review the change, then run /dmx/commit to save your progress.
  - Run /dmx/implement-next-task for the next task.
  - Run /dmx/implement-next-phase to complete the rest of this phase at once.
```

Stop. Do not implement anything else until prompted again.

## Guards

- Execute exactly one task per invocation. Never chain tasks automatically.
- If the task is blocked (depends on something missing or requires a decision not in the spec), stop and report: "Blocked: {reason}. Clarify in spec.md before continuing."
- If implementing this task reveals that a subsequent task in the plan is now irrelevant or needs to change, note it in the report but do not modify tasks.md beyond checking off the current task. Let the developer decide.
- Never delete or reorder tasks in tasks.md. Only change `[ ]` to `[x]` for the current task.
