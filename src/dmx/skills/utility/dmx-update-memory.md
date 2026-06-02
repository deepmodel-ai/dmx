---
name: update-memory
title: Update Memory Bank
description: Review the current ticket's spec and completed tasks, extract durable project learnings, and sync them into the core memory bank files. Run after completing a ticket or a significant phase.
arguments:
  - name: ticket_id
    description: Jira ticket ID to pull learnings from. Auto-detected from activeContext.md if omitted.
    required: false
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

## Step 2 — Read the current ticket's working files

If `{{ticket_id}}` was provided, use it. Otherwise extract from `activeContext.md`.

If a ticket ID is found, read:
- `.dmx/tickets/active/{ticket_id}/spec.md`
- `.dmx/tickets/active/{ticket_id}/tasks.md`

If neither exists or no ticket is active, proceed with Step 3 using only what is visible in the current session's context.

## Step 3 — Extract durable learnings

From the ticket files and recent session context, identify:

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

**For `activeContext.md`:**
- What was just completed
- What the next focus area is
- Any open decisions or unresolved trade-offs
- Learnings that don't fit the other files

Do not extract:
- Implementation details that will change (specific function names, line numbers)
- Information already present in the files
- Notes that are only relevant to the current ticket (those belong in the ticket folder)

## Step 4 — Update each file that needs changes

For each file with new learnings, make targeted additions or amendments. Rules:
- Append to the relevant section; do not rewrite whole files.
- Use clear, present-tense statements ("The service layer uses X pattern for Y reason").
- If a previous statement is now incorrect, update it in-place.
- Keep entries concise — one idea per bullet.

Leave files unchanged if there is nothing new to add. Do not fabricate updates for the sake of appearing thorough.

## Step 5 — Update activeContext.md fully

`activeContext.md` always needs a current-state refresh. Update it to reflect:

```markdown
## Active Ticket
{update or clear depending on whether the ticket is done}

## Recently Completed
- {ticket_id}: {one-line summary of what was done}

## Current Focus
{what is next — next ticket, next phase, or "none"}

## Open Decisions
{any unresolved trade-offs or design questions worth tracking}
```

## Step 6 — Return the result

Output:
```
Memory bank updated.

Files changed:
{list each file that was modified and a one-line summary of what was added}

Files unchanged:
{list files with no updates}

Next:
  - Run /dmx/create-ticket to start the next piece of work.
  - Run /dmx/derive-ticket if you have uncommitted changes to formalise.
```

## Guards

- Never delete existing content from memory bank files — only add or amend.
- Never store ticket-specific implementation details in core files. Those belong in the ticket folder.
- If the ticket's `tasks.md` shows unchecked tasks remaining, note in the output: "Note: {ticket_id} has unchecked tasks — memory was updated with progress so far, not a completed state."
