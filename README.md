# dmx

[![PyPI](https://img.shields.io/pypi/v/deepmodel-dmx)](https://pypi.org/project/deepmodel-dmx/)
[![Test](https://github.com/deepmodel-ai/dmx/actions/workflows/test.yml/badge.svg)](https://github.com/deepmodel-ai/dmx/actions/workflows/test.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

It has never been easier to build software.
It has never been harder to build useful software.

AI generated code is hard to debug, hard to maintain, and dangerous to trust in production. AI-driven tech debt accumulates at machine speed.

dmx gives you an AI-native developer workflow built on two ideas: spec-driven development, where work begins with a detailed spec before any code is written; and a shared memory bank, where project context is stored in the repo so every developer and every AI session starts from the same understanding. It runs inside IDEs like Cursor, Claude Code, GitHub Copilot, and Antigravity, and provides slash commands and always-apply rules that keep you in control of what gets built, how it gets built, and why.

> [!NOTE]
> dmx implements the [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc) — an open framework for disciplined AI-native engineering.

## How it works

dmx is implemented as an MCP server. Connect it to your IDE ([get started ↓](#get-started-in-3-steps)) and you get access to a series of skills and rules that orchestrate the developer workflow. Use them to create tickets, name branches, write commits, draft PRs, and publish releases. The AI implements each phase and stops — you review, commit, and decide what comes next.

Before running any command, run `/dmx/init` once per project. It writes the always-apply rules into your IDE and initializes the `.dmx/` memory bank — persistent project context that every future AI session reads before doing anything.

| Phase | Command | What it does |
|---|---|---|
| **Specify** | Option A: `/dmx/create-ticket` | Describe the work. dmx creates a ticket, names and checks out the branch, scaffolds a spec pre-filled with project context. AI asks clarifying questions — you answer before any code is written. |
| **Specify** | Option B: `/dmx/derive-ticket` | Already wrote some code? dmx reads your uncommitted changes, infers what was built, creates the ticket, moves you to a properly named branch, and scaffolds the spec based on your code. |
| **Plan** | `/dmx/plan` | Reads the answered spec and generates a phased task list. You review it before implementation starts. |
| **Build** | Option A: `/dmx/implement-next-phase` | Implements every task in the next phase, then stops. You review the output, run `/dmx/commit` to write the commit message, and move forward. |
| **Build** | Option B: `/dmx/implement-next-task` | Implements a single task and stops. Use this for fine-grained control when tasks are large or risky. |
| **Validate** | `/dmx/validate` | Runs a quality gate against spec, security, and coverage. |
| **Validate** | `/dmx/create-pr` | Syncs the memory bank, drafts the PR body, and opens the pull request. |
| **Ship** | `/dmx/create-release` | Tags the release and publishes it. You confirm before it goes out. |
| **Ship** | `/dmx/close-ticket` | After the PR is merged: transitions the ticket to Done, deletes the branch, and archives the spec. |

![AI SDLC Phase Arc](https://raw.githubusercontent.com/deepmodel-ai/ai-sdlc/main/assets/phase-arc.drawio.svg)


## Get started in 3 steps

### Step 1 — Add the MCP server

Add to your Cursor MCP config (`~/.cursor/mcp.json`) and restart:

```json
{
  "mcpServers": {
    "dmx": {
      "command": "uvx",
      "args": ["--from", "deepmodel-dmx", "dmx", "serve"]
    }
  }
}
```

<details>
<summary>Claude Code, Copilot, Antigravity →</summary>

**Claude Code** — `~/.claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "dmx": {
      "command": "uvx",
      "args": ["--from", "deepmodel-dmx", "dmx", "serve"]
    }
  }
}
```

**GitHub Copilot** — `.vscode/settings.json`

```json
{
  "mcp": {
    "servers": {
      "dmx": {
        "command": "uvx",
        "args": ["--from", "deepmodel-dmx", "dmx", "serve"]
      }
    }
  }
}
```

**Antigravity** — `~/.gemini/config/mcp_config.json`

```json
{
  "mcpServers": {
    "dmx": {
      "command": "uvx",
      "args": ["--from", "deepmodel-dmx", "dmx", "serve"]
    }
  }
}
```

</details>

### Step 2 — Run `/dmx/init` in your IDE

Open any chat and run `/dmx/init`. It will:

- Write always-apply engineering rules into your project (`.cursor/rules/`, `CLAUDE.md`, etc.)
- Scaffold the `.dmx/` memory bank — persistent context that every future AI session reads first
- Configure your workflow mode (feature branches or trunk) and ticketing system

Safe to re-run. Updates config without overwriting memory bank files that already have content.

### Step 3 — Start your first ticket

```
/dmx/create-ticket
```

Describe what you want to build. dmx scaffolds the spec, asks clarifying questions, and waits for your answers before writing a line of code.


## Skill catalog

<details>
<summary>All 23 slash commands →</summary>

### Workflow

| Skill | What it does |
|---|---|
| `/dmx/init` | One-time project setup: rules, memory bank, IDE config |
| `/dmx/create-ticket` | Idea → ticket → branch → spec in one command |
| `/dmx/derive-ticket` | Uncommitted changes → ticket → branch → derived spec |
| `/dmx/plan` | Answered spec → phased `tasks.md` |
| `/dmx/implement-next-phase` | Execute the next phase in `tasks.md`, stop |
| `/dmx/implement-next-task` | Execute the next single task, stop |
| `/dmx/validate` | Pre-PR quality gate: ticket, code, security |
| `/dmx/create-branch` | Create a properly named branch, scaffold spec |
| `/dmx/commit` | Conventional commit from staged diff |
| `/dmx/create-pr` | Open PR with correct title + description |
| `/dmx/draft-pr-description` | Generate PR body without opening the PR |
| `/dmx/close-ticket` | Post-merge: close ticket, delete branch, archive |

### Release

| Skill | What it does |
|---|---|
| `/dmx/hotfix` | Create hotfix branch from master |
| `/dmx/draft-release-note` | Generate release notes from merged PRs |
| `/dmx/release-merge` | Open staging → master release gate PR |
| `/dmx/create-release` | Tag and publish GitHub release |

### Utilities

| Skill | What it does |
|---|---|
| `/dmx/status` | Snapshot of in-progress tickets and open PRs |
| `/dmx/sync-branch` | Rebase/merge base branch onto current branch |
| `/dmx/update-memory` | Sync ticket learnings into memory bank |
| `/dmx/review` | Code review: clarity, correctness, maintainability |
| `/dmx/test` | Write tests that enable change |
| `/dmx/docs` | Write clear, human-first documentation |
| `/dmx/secure` | Security analysis — thinks like an attacker |

</details>


## Self-hosting

<details>
<summary>CLI reference and environment variables →</summary>

```
dmx serve               # stdio transport (default)
dmx serve --http        # SSE transport on :8080
dmx serve --http --port 9000
dmx serve --watch       # hot-reload on file change (requires watchfiles extra)
dmx list-skills         # print all loaded skills
```

| Variable | Purpose | Default |
|---|---|---|
| `PORT` | HTTP port | `8080` |
| `DMX_SKILLS_DIR` | Override bundled skills directory | bundled |
| `DMX_RULES_DIR` | Override bundled rules directory | bundled |
| `DMX_IDE` | Force IDE detection | auto-detect |
| `MCP_API_KEY` | Bearer token for HTTP auth | — |
| `REQUIRE_API_KEY` | Enable HTTP bearer auth | `false` |

Requires Python 3.11+ and `uv`.

</details>
