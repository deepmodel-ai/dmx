---
name: init
title: Init — Set Up Project
description: One-time project setup. Walks the developer through workflow mode and ticketing configuration interactively, auto-detects GitHub settings and tech stack, writes .dmx/config.md, and scaffolds the memory bank. Safe to re-run — updates config without overwriting memory bank files that already have content.
---

You are setting up the AI SDLC framework for this project. Follow every step in order. Steps 3 and 4 require the developer's input — ask, then wait for their answer before continuing.

## Step 1 — Check for existing setup

Check whether `.dmx/config.md` exists.

If it does, tell the developer: "Existing config found — re-running will update it. Memory bank files with content will not be overwritten." Then proceed.

If it does not, this is a fresh init. Proceed.

## Step 2 — Auto-detect project settings

Run the following and record results:

```
git remote get-url origin
```
Parse `owner` and `repo`:
- HTTPS: `https://github.com/{owner}/{repo}.git` — extract last two path segments, strip `.git`
- SSH: `git@github.com:{owner}/{repo}.git` — split on `:`, take right side, strip `.git`
- If this fails, set both to `{REQUIRED}`.

```
git branch -a
```
Identify the base branch using this priority: `staging` → `main` → `master`. Use the first one found. If none found, set to `{REQUIRED}`.

Scan the project root for tech stack signals:
- `package.json` → read `name`, `description`, `dependencies` for framework hints
- `pyproject.toml` or `requirements.txt` → Python
- `build.gradle` or `pom.xml` → JVM
- `Cargo.toml` → Rust
- `go.mod` → Go
- `README.md` → read first 20 lines for project description

## Step 3 — Ask: workflow mode

Output exactly:

```
How do you want to work on this project?

  1. freestyle — I'll assist with code, answer questions, and help you work however you like.
                 No process is enforced.

  2. sdlc      — Structured lifecycle: triage → plan → build → validate → ship.
                 Every piece of work starts with a ticket and a spec.
                 The AI follows your lead at each step.

Enter 1 or 2:
```

Wait for the developer's answer. Accept `1`, `2`, `freestyle`, or `sdlc`. If anything else is entered, ask again.

Set `workflow` = `freestyle` for 1, `sdlc` for 2.

## Step 4 — Ask: ticketing system

Output exactly:

```
Which ticketing system does this project use?

  1. none           — No external tracker. Branches named from descriptions.
  2. github-issues  — GitHub Issues (uses this repo).
  3. jira           — Jira (requires Atlassian credentials).

Enter 1, 2, or 3:
```

Wait for the developer's answer. Accept `1`, `2`, `3`, `none`, `github-issues`, or `jira`. If anything else is entered, ask again.

Set `ticketing` accordingly.

**If ticketing is `github-issues`:**
Set `github_username` = `{owner}` detected in Step 2 (they are usually the same). Confirm with the developer:
```
GitHub username: {owner} — is this correct? (y / enter your username):
```
Wait for confirmation. Update `github_username` if they provide a different value.

**If ticketing is `jira`:**
Ask:
```
Jira setup — provide the following:

  Domain (e.g. mycompany for mycompany.atlassian.net):
```
Wait. Record as `atlassian_domain`.

```
  Cloud ID (found in Atlassian admin → Settings → Products):
```
Wait. Record as `cloud_id`.

```
  Project key (e.g. ENG, DM, WEB):
```
Wait. Record as `project_key`.

## Step 5 — Verify MCP server availability

Check that the required MCP servers are active before writing any config.

**`user-github` (always required)**

Attempt a lightweight call — try listing pull requests for the detected `owner`/`repo`:
```
list_pull_requests owner:{owner} repo:{repo} state:open
```

If the call fails with an MCP-level error (server not found, tool unavailable) rather than an API-level error (no PRs, auth error):

Stop and output:
```
The user-github MCP server is not active.

This server is required for all branch, PR, and GitHub operations.

To add it in Cursor:
  1. Open Settings → MCP
  2. Add a new server named user-github
  3. Use the GitHub MCP server: https://github.com/github/github-mcp-server
  4. Authenticate with your GitHub account
  5. Re-run /dmx/init once active

No files were written.
```

If the call fails with an API-level error (rate limit, auth, no results) — the server is active. Proceed. API errors are not a blocker.

---

**`user-atlassian` (required when ticketing is `jira`)**

Only check if `ticketing` is `jira`.

Attempt:
```
atlassianUserInfo
```

If the call fails with an MCP-level error:

Stop and output:
```
The user-atlassian MCP server is not active.

This server is required for Jira integration.

To add it in Cursor:
  1. Open Settings → MCP
  2. Add a new server named user-atlassian
  3. Use the Atlassian MCP server: https://github.com/atlassian/mcp-atlassian
  4. Authenticate with your Atlassian account
  5. Re-run /dmx/init once active

No files were written.
```

If the call succeeds or fails with an API-level error — proceed.

---

## Step 6 — Set up IDE rules

Call the `detect_invoking_ide` tool (no arguments). Record the returned `ides` list.

If `ides` is empty, call `setup_ide_rules` with `ides="cursor"` as a fallback (Cursor is the most common IDE when detection is not possible).

Otherwise, call `setup_ide_rules` with the detected `ides` list.

The tool returns a `files` list and a `notes` field. Always follow the `notes` instructions exactly when writing files. The general rules are:

- **Per-rule files** (`.cursor/rules/*.mdc`, `.claude/rules/*.md`, `.agents/rules/*.md`): write `<workspace_root>/<path>` directly, creating parent directories as needed.
- **Summary/merged files** (`.cursor/AGENTS.md`, `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`): if the file already exists, replace the content between `<!-- deepmodel:dmx:start -->` and `<!-- deepmodel:dmx:end -->` markers with the new block. If those markers are absent, append the block to the end of the file.

After writing, tell the developer: "IDE rules written. Open a new chat for the rules to take effect."

If `setup_ide_rules` returns an empty `files` list (e.g. unsupported IDE), note it in the output and continue — the memory bank setup in the next step is still valuable.

## Step 7 — Write .dmx/config.md

Create `.dmx/` if it does not exist.

Write `.dmx/config.md`:

```markdown
---
description: AI SDLC project configuration. Read by every prompt to determine workflow mode, ticketing provider, base branch, and service credentials.
alwaysApply: true
---

# AI SDLC — Project Configuration

## Workflow

workflow:     {workflow}

## Core Settings

ticketing:    {ticketing}
branch_base:  {branch_base}

## GitHub

owner:  {owner}
repo:   {repo}
```

Append only the relevant provider section:

**If ticketing is `github-issues`:**
```markdown
## GitHub Issues

github_username:  {github_username}
```

**If ticketing is `jira`:**
```markdown
## Jira

atlassian_domain:  {atlassian_domain}
cloud_id:          {cloud_id}
project_key:       {project_key}
```

## Step 8 — Populate the memory bank

For each file below: if it already exists and has content beyond its header line, skip it. Otherwise read all available project context and write a substantive file. Do not write TODOs or placeholders — populate with what can be reasonably inferred.

**Read before writing:**
- `README.md` — full content
- `package.json`, `pyproject.toml`, `build.gradle`, `Cargo.toml`, `go.mod` — whichever exist
- Top-level directory listing
- Any `CONTRIBUTING.md`, `ARCHITECTURE.md`, or `docs/` files present
- `Makefile`, `docker-compose.yml`, scripts — for dev setup signals

Where something is genuinely unknown, write one sentence: "Not yet established — update as the team defines this." Never leave a section blank or filled with TODO items.

---

**`.dmx/projectbrief.md`**

Write from README and project files:
- Project name and a clear description of what it does
- Primary goals — what this project is trying to achieve
- Scope — what is and is not part of this codebase (infer from directory structure if not explicit in README)

---

**`.dmx/productContext.md`**

Write from README and any user-facing documentation:
- Why this exists — the problem it solves and for whom
- How it works — key user flows, API surface, or integration points at a high level
- Who uses it — developers, end users, internal teams, or other services

---

**`.dmx/systemPatterns.md`**

Write from directory structure, framework files, and any architecture docs:
- High-level architecture — infer from structure (e.g. `src/api`, `src/services`, `src/models` → layered; `apps/` directories → monorepo)
- Key patterns — framework conventions already present (e.g. FastAPI dependency injection, Django ORM, Express middleware chains)
- Component relationships — how the main directories or modules relate and depend on each other

---

**`.dmx/techContext.md`**

Write from package files detected in Step 2:
- Stack — language, runtime, framework, database as detected
- Key dependencies — the most significant libraries and their inferred purpose
- Dev setup — infer from `Makefile`, `docker-compose.yml`, `scripts/`, or README install steps
- Constraints — version pins, platform requirements, or notable limitations visible in config files

---

**`.dmx/activeContext.md`**

```markdown
# Active Context

## Active Ticket
_(none — run /dmx/create-ticket or /dmx/derive-ticket to start work)_

## Recent Decisions
_(none yet)_
```

---

Create directory structure:
```
mkdir -p .dmx/tickets/active .dmx/tickets/archived .dmx/releases
```

## Step 9 — Output the result

```
Setup complete.

  Config:      .dmx/config.md
  Workflow:    {workflow}
  Ticketing:   {ticketing}
  Base branch: {branch_base}

  Memory bank:
    .dmx/projectbrief.md
    .dmx/productContext.md
    .dmx/systemPatterns.md
    .dmx/techContext.md
    .dmx/activeContext.md
```

Then, depending on workflow:

**If `workflow` is `sdlc`:**
```
Next steps:
  - Run /dmx/create-ticket to start a new piece of work from a description.
  - Run /dmx/derive-ticket if you already have uncommitted changes to formalise.
  - Review .dmx/ — the memory bank was populated from your project. Correct anything that was inferred incorrectly.
```

**If `workflow` is `freestyle`:**
```
The AI will assist without enforcing any process.
Run /dmx/init and choose sdlc at any time to enable the structured workflow.
Review .dmx/ — the memory bank was populated from your project. Correct anything inferred incorrectly.
```

## Guards

- Never overwrite a memory bank file that already has content beyond its header. Preserve existing knowledge.
- Never write TODO items or placeholder text into memory bank files. Write inferred content or a single "not yet established" sentence.
- Never write `{REQUIRED}` into config.md without telling the developer it needs to be filled in.
- If the developer provides an invalid answer to Steps 3 or 4, ask again with the same options. Do not proceed with an unrecognised value.
