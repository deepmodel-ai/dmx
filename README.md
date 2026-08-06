# dmx

[![PyPI](https://img.shields.io/pypi/v/deepmodel-dmx)](https://pypi.org/project/deepmodel-dmx/)
[![Test](https://github.com/deepmodel-ai/dmx/actions/workflows/test.yml/badge.svg)](https://github.com/deepmodel-ai/dmx/actions/workflows/test.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

**The missing orchestrator for AI-native engineering.**

Most teams building with AI run into the same problems:

- Workflow lives in chat history. No process, nothing that persists, nothing you can hand off.
- Every developer is using AI differently. Different tools, different prompts, different output.
- Results are unpredictable. Brilliant one session, wrong the next.
- No shared context. Every session starts from scratch, every developer builds their own mental model.
- Missing context causes drift. The same problem solved three ways in the same codebase.

The [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc) is the framework we built to fix this: start with a spec, build in phases, verify output, keep context in the repo. dmx is the orchestrator that makes the AI SDLC executable.

dmx runs as an MCP server inside Cursor, Claude Code, GitHub Copilot, and Antigravity. It gives you AI skills that govern the full engineering lifecycle — from first spec to production release — as structured loops. Each loop has a skill sequence, a shared memory context, and a validator. A loop without a validator is just a script.

```
/dmx/create-ticket         # describe the work → spec → branch
/dmx/plan                  # spec → phased task list
/dmx/implement-next-phase  # build next phase, stop, wait for review
/dmx/validate              # quality gate: spec, security, coverage
/dmx/create-pr             # sync memory, draft PR body, open PR
/dmx/create-release        # tag and publish the release
```

Every command stops and waits. You review, decide, and move forward. Project context lives in `.dmx/` — committed to the repo, read by every AI session.

When a skill sequence is trusted, formalize it as a **loop** — declarative YAML config, automated validators, and persisted state:

```
/run-loop spec             # start the spec loop (ticket → branch → spec)
/loop-continue             # resume after a human gate
```

## Loop runtime

Loops are declarative configs that run an ordered skill sequence with durable state and automated validation. Default configs ship with dmx; teams override via `.dmx/loops/{name}.yaml`.

**Foreground** is running `/dmx/*` skills manually — you are the orchestrator. **Background** is the loop runtime: you define the sequence, write validators that encode your judgment, and review the artifact. Trust is earned by validators, not a config flag.

| Command | What it does |
|---|---|
| `/run-loop` | Start a loop by name — loads config, writes state, runs the first skill |
| `/loop-continue` | Resume a paused loop after human review at a gate |

Bundled loops for the SDLC pipeline:

| Loop | Skills | Chains to |
|---|---|---|
| `spec` | create-ticket | `plan` |
| `plan` | plan | `dev` |
| `dev` | implement-next-phase, commit | `validate` |
| `validate` | validate | `release` |
| `release` | create-pr, update-memory | — |

Each loop config defines a goal state, optional `repeat_until` condition, validators, human gate policy, and `on_complete` chaining. Run state is written to `.dmx/loop-state.json` (active run) and `.dmx/jobs/{job_id}/` (per-run history).

Validators are plain Python functions at `validators/{name}.py` in the app repo (bundled fallbacks ship with dmx). The orchestrator invokes them via subprocess after all skills complete — the coding agent runs skills; validators run deterministically.

**Status:** config, state persistence, MCP orchestration (`run_loop`, `loop_advance`, `loop_continue`), and human-gate sequencing are implemented. Validator execution, policy engine, `repeat_until` iteration, and loop chaining are in progress ([#5](https://github.com/deepmodel-ai/dmx/issues/5)).

## Roadmap

- [x] Full lifecycle workflow — spec, plan, build, validate, release
- [x] `.dmx/` memory bank — shared project context committed to the repo
- [ ] Loop runtime — validators, policy engine, `repeat_until`, and autonomous chaining ([#5](https://github.com/deepmodel-ai/dmx/issues/5))
- [ ] Team server — hosted MCP endpoint, shared loops and rules across the team
- [ ] Gateway — model governance, cost visibility, autonomous background execution

## Get started

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

Add to your IDE config ([Claude Code, Copilot, Antigravity ↓](#step-1--add-the-mcp-server)). Run `/dmx/init` once per project. Then:

```
/dmx/create-ticket
```

## Learn more

- [When Is a Loop Ready to Run Without You?](https://himakara.hashnode.dev/when-is-a-loop-ready-to-run-without-you) — the thinking behind dmx
- [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc) — the open framework dmx implements

<details>
<summary>Full install guide</summary>

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
<summary>Claude Code, Copilot, Antigravity</summary>

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
- Scaffold the `.dmx/` memory bank — durable core files plus an `activeContext.md` learning inbox
- Configure your workflow mode (feature branches or trunk) and ticketing system

Safe to re-run. Updates config without overwriting memory bank files that already have content.

### Step 3 — Start your first ticket

```
/dmx/create-ticket
```

Describe what you want to build. dmx scaffolds the spec, asks clarifying questions, and waits for your answers before writing a line of code.

</details>

<details>
<summary>Full skill catalog </summary>

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
| `/dmx/close-ticket` | Post-merge: close ticket, delete branch |

### Release

| Skill | What it does |
|---|---|
| `/dmx/hotfix` | Create hotfix branch from `production_branch` |
| `/dmx/draft-release-note` | Generate release notes from merged PRs |
| `/dmx/release-merge` | Open integration → production release gate PR |
| `/dmx/create-release` | Tag production branch and publish GitHub release |

### Utilities

| Skill | What it does |
|---|---|
| `/dmx/status` | Snapshot of in-progress tickets and open PRs |
| `/dmx/sync-branch` | Rebase/merge integration branch onto current branch |
| `/dmx/update-memory` | Deep sync: promote inbox learnings, reconcile contradictions |
| `/dmx/review` | Code review: clarity, correctness, maintainability |
| `/dmx/test` | Write tests that enable change |
| `/dmx/docs` | Write clear, human-first documentation |
| `/dmx/secure` | Security analysis — thinks like an attacker |

### Loop

| Skill | What it does |
|---|---|
| `/run-loop` | Start a loop by name — reads loop config, initialises state, runs first skill |
| `/loop-continue` | Resume a paused loop after human review at a gate |

</details>

<details>
<summary>Memory bank (.dmx/) </summary>

The `.dmx/` directory is the project's shared memory — committed to the repo so every developer and every AI session starts from the same understanding.

| File | Role | Lifetime |
|---|---|---|
| `config.md` | Project settings — ticketing, integration/production branches, credentials; injected as always-apply rule | Updated by `/dmx/init` |
| `projectbrief.md` | Goals, scope, non-negotiables | Durable — updated rarely |
| `productContext.md` | User-facing behaviour and flows | Durable — updated when features ship |
| `systemPatterns.md` | Architecture, patterns, component relationships | Durable — updated when design changes |
| `techContext.md` | Stack, dependencies, constraints | Durable — updated when tooling changes |
| `activeContext.md` | Learning inbox: open learnings, decisions, session notes | Branch-local — promoted to durable files on commit/PR |
| `spec.md` | What is being built and why — YAML frontmatter + scope + Q&A | Branch-scoped — created by `create-ticket`, committed with the PR |
| `tasks.md` | Phased implementation plan | Branch-scoped — created by `plan`, committed with the PR |
| `loops/` | Loop config overrides (YAML) | Optional — overrides bundled defaults from dmx |
| `loop-state.json` | Active loop run pointer | Ephemeral — written during a loop run |
| `jobs/` | Per-run loop state (skill progress, validator results) | Branch-scoped — committed with the PR when present |

**Branch-as-identity model**: each branch holds exactly one unit of work. `spec.md` and `tasks.md` live directly in `.dmx/` on the feature branch. When a PR merges, they go with it; the next branch starts fresh.

**Three-tier memory sync**: learnings accumulate in `activeContext.md` during implementation. `/dmx/commit` promotes qualifying items (light sync), `/dmx/create-pr` promotes all remaining items (full sync), and `/dmx/update-memory` does a deep reconciliation on demand.

</details>
