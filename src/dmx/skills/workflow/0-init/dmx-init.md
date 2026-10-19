---
name: init
title: Init — Set Up Project
description: One-time project setup. Walks the developer through workflow mode and ticketing configuration interactively, auto-detects GitHub settings and tech stack, writes .dmx/config.md, and scaffolds the memory bank. Safe to re-run — updates config without overwriting memory bank files that already have content.
---

You are setting up the AI SDLC framework for this project. Follow every step in order. Steps 4 and 5 require the developer's input, and Step 9 does in a workspace repo — ask, then wait for their answer before continuing.

## Step 1 — Check for existing setup

Check whether `.dmx/config.md` exists.

If it does, read the existing file and record any values already set for `branch_base` and `production_branch`. Tell the developer: "Existing config found — re-running will update it. Memory bank files with content will not be overwritten. Existing `production_branch` will be kept." Then proceed.

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

From the branch list, treat a name as present if it appears as a local branch or as `origin/{name}` / `remotes/origin/{name}`.

**Integration branch (`branch_base`)** — feature PRs merge here; default diff base. Use this priority; take the first found: `staging` → `main` → `master`. If none found, set to `{REQUIRED}`.

**Production branch (`production_branch`)** — releases, hotfixes, and tags target here. Skip detection if Step 1 already recorded `production_branch` from existing config.

Otherwise:
- If only `master` exists → `production_branch` = `master`
- If only `main` exists → `production_branch` = `main`
- If **both** `master` and `main` exist → ask:
  ```
  Both master and main exist. Which is your production branch (releases and hotfixes target here)?

    1. master
    2. main

  Enter 1 or 2:
  ```
  Wait for the answer. Accept `1`, `2`, `master`, or `main`.
- If neither exists → set to `{REQUIRED}` and tell the developer it must be set manually.

If `staging`, `master`, and `main` are all present and `production_branch` was just resolved (detected or answered), confirm both roles:
```
Confirm branch roles:

  Integration branch: {branch_base}
  Production branch:  {production_branch}

Correct? (y / enter corrections — integration: {branch}, production: {branch})
```
Wait. If they provide corrections, update `branch_base` and/or `production_branch` accordingly.

Scan the project root for tech stack signals:
- `package.json` → read `name`, `description`, `dependencies` for framework hints
- `pyproject.toml` or `requirements.txt` → Python
- `build.gradle` or `pom.xml` → JVM
- `Cargo.toml` → Rust
- `go.mod` → Go
- `README.md` → read first 20 lines for project description

## Step 3 — Check GitHub access

Check this before asking anything, so a developer who has to set it up doesn't answer the questions twice.

dmx reaches GitHub through the GitHub MCP server (`user-github`) or, without it, the `gh` CLI.

1. Try the server. List pull requests for the detected `owner`/`repo`:
   ```
   list_pull_requests owner:{owner} repo:{repo} state:open
   ```
   If it answers, even with an API-level error (no PRs, rate limit, auth), record `github_access` = `mcp` and continue.
2. If the server isn't connected (MCP-level error: server not found, tool unavailable), try the CLI:
   ```
   gh auth status
   gh repo view {owner}/{repo} --json name
   ```
   If both succeed, record `github_access` = `gh`, tell the developer "No GitHub MCP server is connected, so dmx will use the `gh` CLI for branches, PRs and issues.", and continue.
3. If neither works, stop and output:
```
dmx needs GitHub access, through the GitHub MCP server or the gh CLI. Neither is set up.

Either sign in to the gh CLI:
  gh auth login

Or add the GitHub MCP server (https://github.com/github/github-mcp-server) as user-github:
  Claude Code:  claude mcp add --transport http user-github https://api.githubcopilot.com/mcp/
  Cursor:       Settings → MCP → add a server named user-github
  VS Code:      add a user-github server to .vscode/mcp.json, or Copilot's mcp-config.json

Then re-run /dmx/init. No files were written.
```

## Step 4 — Ask: workflow mode

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

## Step 5 — Ask: ticketing system

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

Then check the Atlassian MCP server (`user-atlassian`) right away:
```
atlassianUserInfo
```
If it answers, even with an API-level error, continue. If the server isn't connected (MCP-level error), stop and output:
```
Jira needs the Atlassian MCP server (https://github.com/atlassian/mcp-atlassian) as user-atlassian.

  Claude Code:  claude mcp add user-atlassian …  (the command or URL from the server's setup guide)
  Cursor:       Settings → MCP → add a server named user-atlassian
  VS Code:      add a user-atlassian server to .vscode/mcp.json, or Copilot's mcp-config.json

Sign in with your Atlassian account, then re-run /dmx/init. No files were written.
```

## Step 6 — Set up IDE rules

Call the `detect_invoking_ide` tool (no arguments). Record the returned `ides` list.

If `ides` is empty, call `setup_ide_rules` with `ides="cursor"` as a fallback (Cursor is the most common IDE when detection is not possible).

Otherwise, call `setup_ide_rules` with the detected `ides` list.

The tool returns a `files` list and a `notes` field. Always follow the `notes` instructions exactly when writing files.

Write each returned file as the complete file at <workspace_root>/<path>, creating parent directories as needed. Per-rule files (.cursor/rules/*.mdc, .claude/rules/*.md, .agents/rules/*.md) and summary files (.cursor/AGENTS.md, CLAUDE.md, AGENTS.md, .github/copilot-instructions.md) are both complete file contents. A dmx block runs from any line that starts with `<!-- deepmodel:dmx:start` to the next `<!-- deepmodel:dmx:end -->`. The returned summary file already has every old dmx block removed and the new block written once, where the first old block was. If there was no dmx block, the block is appended. Content outside dmx blocks is never changed. Do not splice markers yourself. After writing, open a new chat for the rules to take effect.

After writing, tell the developer: "IDE rules written. Open a new chat for the rules to take effect."

If `notes` lists files that could not be read or have no end to their dmx block, tell the developer which files were not changed and that they need to be fixed by hand before re-running `/dmx/upgrade`.

If `setup_ide_rules` returns an empty `files` list (e.g. unsupported IDE), note it in the output and continue — the memory bank setup in the next step is still valuable.

**Ignored rule files.** Run `git check-ignore -v` on every file you wrote. If one is ignored (for example `.claude/*` in `.gitignore`), it won't be committed, so teammates won't get the dmx rule. Show the matching `.gitignore` line and offer to add a negation after it, such as `!.claude/rules/` (when a whole folder is ignored, as with `.claude/`, replace that line with `.claude/*` and add the negation after it). After adding it, run `git check-ignore` again to confirm the file is no longer ignored. If the developer declines, tell them each teammate has to run `/dmx/init` or `/dmx/upgrade` themselves to get the rule.

## Step 7 — Write .dmx/config.md

Create `.dmx/` if it does not exist.

If `.dmx/config.md` already exists and contains `production_branch:`, keep the existing value — do not overwrite it. Write or update all other fields from this init run.

When a skill reads config and `production_branch` is absent: auto-detect only when unambiguous (sole `master` or sole `main` in the repo); if both exist, stop and ask the user to set config or re-run `/dmx/init` — do not guess.

Write `.dmx/config.md`:

```markdown
---
description: AI SDLC project configuration. Read by every prompt to determine workflow mode, ticketing provider, integration branch, production branch, and service credentials.
alwaysApply: true
---

# AI SDLC — Project Configuration

## Workflow

workflow:     {workflow}

## Core Settings

ticketing:           {ticketing}
branch_base:         {branch_base}         # integration — feature PRs merge here
production_branch:   {production_branch}   # production — releases, hotfixes, tags

## GitHub

owner:          {owner}
repo:           {repo}
github_access:  {github_access}   # mcp (GitHub MCP server) or gh (gh CLI)
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

Do not ask about more destinations. Tell the developer that a monorepo whose services file tickets in more than one place can add `jira_projects: [LEDG, PAY]` under `## Jira`, or `github_issue_repos: [owner/repo, owner/other-issues]` under `## GitHub Issues`; `project_key` and `owner/repo` stay the defaults, and a component's `ticket` in its `component.yaml` sets its own default.

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

Do not write `.dmx/activeContext.md`, `.dmx/spec.md` or `.dmx/tasks.md`. Each ticket gets its own in `.dmx/jobs/<job>/`.

Create directory structure:
```
mkdir -p .dmx/releases .dmx/jobs
```

Add this line to `.gitattributes` at the repo root, creating the file if needed, unless the file already has it. It collapses loop state in PR diffs while the spec and task list stay visible:
```
.dmx/jobs/**/*.json linguist-generated=true
```

## Step 9 — Components (monorepos only)

Call `detect_components` on `user-dmx`. It reads the repo's pnpm, npm, yarn, `go.work`, `uv` and Cargo workspace files and returns the members it would make into components, each with an `id`, `path`, `description` and `component_yaml`, plus `ask` and `codeowners`.

**If `ask` is false**, skip this step without saying anything. A single-product repo, or a workspace with one member, is not asked about components.

**If `ask` is true**, show the list and ask once:
```
This repo is a workspace. dmx can treat each package as a component, so each ticket is
scoped to the packages it touches:

  {id}    {path}    {description}
  ...

Create these components? (yes / no / list the ids to keep or rename)
```

Wait for the answer.
- **no:** skip the rest of this step.
- **yes, or a list:** call `detect_components` again with `apply` true and `ids` set to the ids to keep. To rename one, write its `component_yaml` to `.dmx/components/{new id}/component.yaml` yourself instead (ids are lowercase letters, digits, `-` and `_`). Existing component files are never overwritten.

Then:
1. Tell the developer to fill in `depends_on` in each `component.yaml` where one component uses another (for example `depends_on: [ledger]`), so its tests run when that dependency changes. dmx does not guess dependencies.
   Also suggest a `test_command:` line in `.dmx/config.md` for changes outside every component (a root `go.mod`, a lock file, CI files), such as `test_command: make test`. Without it, those changes run every component's tests.
   **Tests outside the packages.** When the repo keeps tests outside the member folders (for example a root `tests/` with a folder per package), add each component's test folders or files to its `paths`, such as `paths: ["pkg_a/**", "tests/pkg_a/**"]`. A Python component whose own folder holds no tests then runs those paths with pytest from the repo root. **Copy CI's test setup.** Before writing a component's `test` command, read the repo's CI workflow (`.github/workflows/*.yml` or similar) for the job that runs that component's tests, and copy its environment variables (such as `CI=true` or flags that turn off checks CI doesn't run locally) and its pytest flags into the command, as `VAR=value uv run …`. Tell the developer which settings came from CI. When its tests need more than that (extras, environment variables, `-n auto`), give it a command instead: `test: {cmd: "uv run --all-extras pytest -q tests/pkg_a", cwd: "."}` in its `component.yaml` (`cwd` is relative to the repo root and defaults to it). `get_context` and `dmx check` show the command each component runs. A test that fails on the base branch too, or only on some machines, goes in that entry's `known_failures: [test id, ...]` rather than being deselected, so it still runs and stays visible in `dmx check`. An entry matches every failing test whose line contains it, so a file (`tests/test_x.py`), a class (`TestEmbeddings`) or a test name covers all the tests in it.
2. Show the `codeowners` lines from the tool and suggest adding them to `.github/CODEOWNERS`, replacing each `@<...-team>` with the real team. `.dmx/` belongs to the platform team, each component folder to its team, and `.dmx/jobs/` has no owner, so a ticket's own files never need platform approval.
3. Offer to move component-specific memory. Read the repo-wide memory files written in Step 8. For each new component, find the parts that are only about it (its architecture, its dependencies, its constraints). Show the move as a diff: removed from `.dmx/{file}`, added to `.dmx/components/{id}/{file}` under the same heading. Ask once for all of them. Apply only what the developer accepts. Content about more than one component stays repo-wide.

## Step 10 — Output the result

```
Setup complete.

  Config:              .dmx/config.md
  Workflow:            {workflow}
  Ticketing:           {ticketing}
  Integration branch:  {branch_base}
  Production branch:   {production_branch}

  Memory bank:
    .dmx/projectbrief.md       ← durable project knowledge
    .dmx/productContext.md     ← durable project knowledge
    .dmx/systemPatterns.md     ← durable project knowledge
    .dmx/techContext.md        ← durable project knowledge

  {if components were created:}
  Components:          {ids, comma-separated} in .dmx/components/

  Each ticket's spec.md, tasks.md and activeContext.md are created in .dmx/jobs/<job>/.
```

Then, depending on workflow:

**Commit the setup.** The setup is still uncommitted, and `/dmx/run-loop spec` starts from `{branch_base}`, so the setup must be on `{branch_base}` first. Ask:
```
The dmx setup isn't committed yet. Loops start from {branch_base}, so it needs to land there first.

  1. Commit it on {branch_base} and push (if you can push to {branch_base} directly)
  2. Commit it on a branch named chore-dmx-setup and open a PR; merge the PR before starting a loop
  3. Leave it uncommitted for now

Enter 1, 2 or 3:
```
Wait for the answer and do it. For 2, open the PR with the GitHub access from Step 3.

**If `workflow` is `sdlc`:**
```
Next steps:
  - Run /dmx/run-loop spec <description> to start a new piece of work (once the setup is on {branch_base}).
  - Run /dmx/derive-ticket if you already have uncommitted changes to formalise.
  - Review .dmx/ — confirm the memory bank was populated correctly.
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
