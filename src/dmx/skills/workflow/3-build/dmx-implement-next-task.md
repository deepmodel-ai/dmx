---
name: implement-next-task
title: Implement Next Task
description: Execute the single next unchecked task in tasks.md and stop. Use this for fine-grained control when tasks are large or risky and you want to review each one individually before proceeding.
---

You are implementing one task from the current branch's plan. Execute exactly one task and stop. Do not move on without being asked again.

## Step 1 — Load configuration and protected branch check

The project configuration is injected into your context as a rule. Extract `branch_base` and `production_branch`. If not available in context, fall back to reading `.dmx/config.md`.

Run:
```
git branch --show-current
```

Protected branches: `master`, `main`, `staging`, `development`, plus `{config.branch_base}` and `{config.production_branch}` when set in config (treat duplicate names once).

If the current branch is one of the protected branches:
1. Tell the user which branch they are on.
2. Ask explicitly: "You are on `{branch}`. Implementing directly on this branch is usually unintentional. Do you want to proceed?"
3. Stop and wait for their answer. Do not implement until they confirm.

If they decline, stop. If they confirm, or the branch is not protected, continue.

## Step 2 — Read spec.md and tasks.md

Read both:
- `.dmx/spec.md` — for context and technical approach
- `.dmx/tasks.md` — for the task list

If `spec.md` does not exist, stop: "spec.md not found. Run `/dmx/create-ticket` or `/dmx/create-branch` first."

If `tasks.md` does not exist, stop: "tasks.md not found. Run `/dmx/plan` to generate the task plan first."

## Step 3 — Find the next unchecked task

Scan tasks.md top-to-bottom, phase by phase, and find the first unchecked task (`- [ ]`).

Note which phase it belongs to.

If all tasks are checked, stop and output:
```
All tasks complete.

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

If you notice a pattern or decision worth capturing, append a brief note to the `## Open Learnings` section in `.dmx/activeContext.md`.

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
