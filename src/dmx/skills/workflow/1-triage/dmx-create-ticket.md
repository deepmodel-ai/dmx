---
name: create-ticket
title: Create Ticket — Start New Work
description: Takes a short task description, reads project context from the memory bank, creates a ticket, creates a branch, checks it out, and scaffolds a spec.md pre-filled with project context. One command to go from idea to ready-to-implement.
arguments:
  - name: task
    description: Short description of the work. Can be a feature, bug, story, or chore — type is inferred.
    required: true
  - name: type
    description: "Override the inferred branch/ticket type. Accepted values: feature, bug, chore. Inferred from task description if omitted."
    required: false
  - name: priority
    description: "Jira priority. High, Medium, Low. Defaults to Medium. Ignored for other providers."
    required: false
---

You are starting a new piece of work. Follow every step in order.

## Step 1 — Load project configuration

The project configuration is injected into your context as a rule. Extract:
- `ticketing` → `none` | `jira` | `github-issues`
- `workflow` → `freestyle` | `sdlc`
- `branch_base` → integration branch (feature PR target)
- `production_branch` → production branch (when set in config; used in behind-production guard)
- `atlassian_domain`, `cloud_id`, `project_key` → Jira coordinates (only when ticketing is `jira`)
- `owner`, `repo` → GitHub coordinates

If configuration is not available in context, fall back to reading `.dmx/config.md`. If neither is found, stop: "Project configuration not found. Run /dmx/init to set up this project."

## Step 2 — Read project context

Read the following memory bank files:
- `.dmx/projectbrief.md` — project goals and scope
- `.dmx/systemPatterns.md` — architecture, patterns, component relationships
- `.dmx/techContext.md` — stack, dependencies, constraints

If `.dmx/` does not exist, stop: "Memory bank not found. Run /dmx/init to set up this project."

Store this context. It will inform the ticket content, technical approach, and spec questions.

## Step 3 — Infer type

If `{{type}}` was provided, use it. Accepted values: `feature`, `bug`, `chore`.

If not provided, infer from `{{task}}`:
- Signals for **bug**: "fix", "broken", "error", "crash", "fails", "incorrect", "regression", "not working"
- Signals for **chore**: "update", "upgrade", "refactor", "clean up", "remove", "deprecate", "migrate", "dependency", "rename"
- Default to **feature** for anything else

Store as `branch_type` (`feature` | `bug` | `chore`).

For Jira `issueTypeName` mapping: `feature` → `Story`, `bug` → `Bug`, `chore` → `Task`.
For GitHub Issues label: use `branch_type` as-is.

## Step 4 — Draft ticket content

Using `{{task}}` and the project context from Step 2, generate a `summary` and `description`.

### Summary
Single sentence, max 80 characters, imperative present tense, capital first letter, no trailing period. Specific enough that scope is clear without reading the description.

Use project context to make it precise — name the affected module, service, or component if inferable.

Examples:
- `Add rate limiting to the public inference endpoint`
- `Fix null pointer in auth token refresh when session expires`
- `Migrate user service from SQLAlchemy 1.x to 2.x`

### Description
```markdown
## Context
{1–3 sentences: why this work is needed. Use project context to explain how it fits the system.}

## What Needs to Be Done
{Concrete bullet list. Informed by system patterns — name specific files, services, or layers where known.}
- ...

## Acceptance Criteria
{Verifiable, testable conditions for Done.}
- [ ] ...

## Notes
{Optional: constraints, dependencies, or integration points visible from project context.}
```

Rules: write for the implementer. For bugs, lead with observed vs expected behaviour. Acceptance criteria must be independently verifiable.

## Step 5 — Create the ticket

**If `ticketing` is `jira`:**

Call `atlassianUserInfo` on `user-atlassian` to get `account_id`.

Call `createJiraIssue` on `user-atlassian`:
```
cloudId:              {config.cloud_id}
projectKey:           {config.project_key}
issueTypeName:        {Jira type from Step 3}
summary:              {summary}
description:          {description}
assignee_account_id:  {account_id}
contentFormat:        markdown
additional_fields:
  priority:
    name: {priority — default Medium}
```
Store `ticket_ref` = returned key (e.g. `DM-1667`).
Store `ticket_url` = `https://{config.atlassian_domain}.atlassian.net/browse/{ticket_ref}`.

---

**If `ticketing` is `github-issues`:**

Call `create_issue` on `user-github`:
```
owner:   {config.owner}
repo:    {config.repo}
title:   {summary}
body:    {description}
labels:  ["{branch_type}"]
```
Store `ticket_ref` = `gh-{number}`.
Store `ticket_url` = `{html_url}`.

---

**If `ticketing` is `none`:**

No ticket is created. `ticket_ref` = `none`. `ticket_url` = none.

## Step 6 — Construct the branch name

Slugify the summary:
- Lowercase, hyphens for spaces, remove special characters, collapse consecutive hyphens, truncate to 60 chars

| Provider | Format | Example |
|---|---|---|
| `jira` | `{branch_type}-{ticket_ref_lowercase}-{slug}` | `feature-dm-1667-add-rate-limiting` |
| `github-issues` | `{branch_type}-gh-{number}-{slug}` | `feature-gh-123-add-rate-limiting` |
| `none` | `{branch_type}-{slug}` | `feature-add-rate-limiting` |

## Step 7 — Create the remote branch and check out locally

Call `create_branch` on `user-github`:
```
owner:       {config.owner}
repo:        {config.repo}
branch:      {branch_name}
from_branch: {config.branch_base}
```

If the branch already exists, stop: "Branch `{branch_name}` already exists on origin."

Run:
```
git fetch origin
git checkout {branch_name}
```

## Step 8 — Scaffold spec.md

Write `.dmx/spec.md`:

```markdown
---
ticket: {ticket_ref or ""}
branch: {branch_name}
summary: {summary}
ticketing: {ticketing}
---

# {summary}

**Ticket:** {ticket link or "(no ticketing system)"}
**Type:** {branch_type}
**Branch:** {branch_name}

---

## Context
{From ticket description Step 4 — why this work is needed in this project}

## Scope
{Bullet list of what is included. Use project patterns to name specific layers, services, or files.}
- ...

## Out of Scope
{Anything explicitly excluded. Flag anything that might seem in scope but isn't.}

## Technical Approach
{Draft a starting point using systemPatterns.md and techContext.md:
 - Which layer(s) of the system are affected
 - Which existing patterns apply (e.g. "follows the repository pattern used in other services")
 - Any known constraints from techContext (e.g. "must use the existing Redis client, not a new connection")
 Leave a TODO if the approach is genuinely unknown.}

---

## Questions
<!-- Answer each question before running /dmx/plan. -->

{2–5 clarifying questions grounded in this project's architecture and stack. Do not ask generic questions.
 Focus on:
  - Design decisions specific to how this codebase is structured
  - Integration points with components named in systemPatterns.md
  - Edge cases relevant to the tech stack in techContext.md
  - Scope boundaries that could expand silently}

1. {Question}
   Answer:

2. {Question}
   Answer:
```

**Ticket link format:**
- Jira: `[{ticket_ref}]({ticket_url})`
- GitHub Issues: `[#{number}]({ticket_url})`
- none: _(no ticketing system)_

When `ticketing` is `none`, omit `ticket` from frontmatter.

## Step 9 — Transition to In Progress

**If `ticketing` is `jira`:**
Call `getTransitionsForJiraIssue` on `user-atlassian`. Find `In Progress`. Call `transitionJiraIssue`.

**If `ticketing` is `github-issues`:**
Call `update_issue` on `user-github` to add label `in-progress`:
```
owner:        {config.owner}
repo:         {config.repo}
issue_number: {number}
labels:       ["in-progress"]
```

**If `ticketing` is `none`:** Skip.

## Step 10 — Return the result

```
{if ticketing ≠ none} Ticket: {ticket_ref} — {summary}
{if ticketing ≠ none} URL:    {ticket_url}
Branch: {branch_name}
Spec:   .dmx/spec.md
```

Then:
```
Spec is pre-filled with project context. Review it, then:
  1. Fill in the Technical Approach if left incomplete.
  2. Answer the Questions.
  3. Run /dmx/plan to generate the phased task plan.
```

## Guards

- Do not create a ticket with a summary longer than 80 characters. Shorten it first.
- If ticket creation fails (API error), stop before creating the branch. Surface the full error.
- If `branch_base` in config appears far behind `{config.production_branch}`, warn: "Base branch may be behind the production branch — consider syncing before branching."
- Never use for hotfixes. If `{{task}}` contains "hotfix" or "production incident", stop: "Use /dmx/hotfix for production incidents."
