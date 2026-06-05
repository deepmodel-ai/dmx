---
name: update-memory
title: Update Memory Bank
description: Review spec.md, tasks.md, and the activeContext learning inbox; extract durable project learnings; reconcile contradictions; and sync everything into the core memory bank files. On-demand escape hatch — /dmx/commit and /dmx/create-pr handle automatic updates during normal workflow.
---

You are updating the project memory bank with durable learnings from recent work. Follow every step in order. Be precise — only write things that will still be true and useful in future sessions.

## Step 1 — Read all core memory bank files

Read each of the following files in full before making any changes:
- `.dmx/projectbrief.md`
- `.dmx/productContext.md`
- `.dmx/systemPatterns.md`
- `.dmx/techContext.md`
- `.dmx/activeContext.md`

If any file does not exist, create it with a minimal placeholder header before continuing.

## Step 2 — Read branch working files

Read the following if they exist:
- `.dmx/spec.md` — branch spec with YAML frontmatter
- `.dmx/tasks.md` — phased task list

If neither exists, proceed with Step 3 using only what is visible in the current session's context and the core files read in Step 1.

## Step 3 — Extract durable learnings

From the working files, active context, and recent session context, identify what is durable across branches:

**For `systemPatterns.md`:**
- New architectural patterns introduced or discovered
- Design decisions made with their rationale
- Component relationships that changed
- Critical implementation patterns others must follow

**For `techContext.md`:**
- New dependencies added and why
- New environment variables or configuration
- New dev tooling or scripts
- Any constraints discovered (performance, security, compatibility)

**For `productContext.md`:**
- Changes to how the product works from a user's perspective
- New features, flows, or capabilities added

**Promote all inbox items from `activeContext.md`:**
Scan `## Open Learnings` and `## Open Decisions`. For every item, ask: _"Is this still true and useful beyond this branch?"_ If yes, write it to the appropriate core file above (or update an existing entry if it contradicts or extends one). Remove the item from `activeContext.md` after writing it. If an item is branch-specific implementation detail, remove it without promoting.

Do not extract:
- Implementation details that will change (specific function names, line numbers)
- Information already present in the files
- Notes that are only relevant to the current branch's spec or tasks

**Reconcile contradictions:** If a new learning contradicts an existing statement in a core file, update the existing statement in-place rather than appending a conflicting line.

## Step 4 — Update each core file that needs changes

For each file with new learnings, make targeted additions or amendments:
- Append to the relevant section; do not rewrite whole files.
- Use clear, present-tense statements ("The service layer uses X pattern for Y reason").
- If a previous statement is now incorrect, update it in-place.
- Keep entries concise — one idea per bullet.

Leave files unchanged if there is nothing new to add. Do not fabricate updates.

## Step 5 — Refresh activeContext.md

After promoting inbox items, rewrite `activeContext.md` to the learning-inbox structure. Preserve any items not yet promoted (i.e. items that are branch-specific and belong in the next session):

```markdown
## Open Learnings
{Any learnings from this session not yet ready to promote, or newly observed patterns still being validated.
 Leave empty if all items were promoted.}

## Open Decisions
{Unresolved trade-offs or design questions that need a decision.
 Leave empty if all items were resolved or promoted.}

## Session Notes
{Brief log of recent activity — one line per commit or significant context shift.
 Keep the most recent 5–10 entries; trim older ones.}
```

Do not add an `## Active Ticket` section. Branch identity lives in `spec.md`, not here.

## Step 6 — Return the result

Output:
```
Memory bank updated.

Files changed:
{list each file that was modified and a one-line summary of what was added or amended}

Files unchanged:
{list files with no updates}

activeContext inbox:
  Promoted: {N} items
  Remaining: {M} items

Next:
  - Run /dmx/create-ticket to start the next piece of work.
  - Run /dmx/derive-ticket if you have uncommitted changes to formalise.
```

## Guards

- Never delete existing content from core files — only add or amend.
- Never store ticket-specific implementation details (function names, line numbers, single-branch decisions) in core files. Those belong in `spec.md` or `tasks.md`.
- If `tasks.md` shows unchecked tasks remaining, note in output: "Note: tasks.md has unchecked tasks — memory updated with progress so far, not a completed state."
- After Step 5, `activeContext.md` must not contain an `## Active Ticket` section.
