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
| `release` | create-pr | — |

Each loop config defines a goal state, optional `repeat_until` condition, validators, human gate policy, and `on_complete` chaining. Run state is written to `.dmx/jobs/{job_id}/{loop_name}-{task_id}.json` — there's no separate active-run pointer; the active run is derived by scanning a job's state files for the one non-terminal (`pending`/`running`/`paused`/`iterating`) entry, keyed off the current branch/ticket. This keeps loop state isolated per branch: pausing work on one branch and running a loop on another can't corrupt or lose either one's state.

Validators are plain Python functions at `validators/{name}.py` in the app repo (bundled fallbacks ship with dmx). The orchestrator invokes them via subprocess after all skills complete — the coding agent runs skills; validators run deterministically.

**Status:** M1 is complete ([#5](https://github.com/deepmodel-ai/dmx/issues/5)) — config, state persistence, MCP orchestration (`run_loop`, `loop_advance`, `loop_continue`), human-gate sequencing, validator execution, the policy engine, `repeat_until` iteration, `on_complete` auto-chaining, and loop-level memory hooks are all implemented and covered by an end-to-end integration test suite that runs the full `spec → plan → dev → validate → release` pipeline through the real MCP tools.

Loop-level memory hooks: before the first skill runs, the runtime surfaces `activeContext.md`'s Open Learnings / Open Decisions to the agent; when a loop finishes (complete, paused for validator review, or iterating), it appends a one-line breadcrumb to Session Notes. This is a deterministic log entry, not judgment — promoting it into durable knowledge is still `/dmx/update-memory`'s job.

## Roadmap

- [x] Full lifecycle workflow — spec, plan, build, validate, release
- [x] `.dmx/` memory bank — shared project context committed to the repo
- [x] Loop runtime — background execution engine with validators, policy, `repeat_until`, and autonomous chaining ([#5](https://github.com/deepmodel-ai/dmx/issues/5))
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

Add to your IDE config ([Claude Code, Copilot, Antigravity ↓](#step-1--add-the-mcp-server)). Then follow [Your first project](#your-first-project) to initialize and run the full loop on a new repo.

## Your first project

A brand-new repo, from `/dmx/init` through the first merged PR. You review at every gate; `/dmx/loop-continue` is how you move forward.

### Before you start

- A GitHub repo cloned locally with an `origin` remote. The spec loop creates the feature branch on GitHub from `origin/{branch_base}` (usually `main` or `master`), even if you track work in Jira or use no ticketing system.
- The dmx MCP server added to your IDE ([install guide](#step-1--add-the-mcp-server)).
- The GitHub MCP server (`user-github`) authenticated. `/dmx/init` probes it before writing any files.
- The Atlassian MCP server only if you will choose Jira at init.

### 1. Initialize

On the integration branch (`main` or `master`):

```
/dmx/init
```

Choose a workflow — **sdlc** (this walkthrough) or **freestyle** (no process enforced) — and a ticketing system: `none`, `github-issues`, or `jira`. Re-run `/dmx/init` at any time to switch workflow or ticketing; existing memory bank files with content are left intact.

Init writes `.dmx/config.md` and the memory bank (`projectbrief.md`, `productContext.md`, `systemPatterns.md`, `techContext.md`, `activeContext.md`). Open a **new chat** so the IDE rules take effect.

If you already have a `docs/` folder, init reads it when populating the memory bank. Writing the requirement first is slightly better; either order works.

### 2. Write the product requirement

Create `docs/requirements.md` at the repo root and put the product or feature description there. You can paste the requirement into chat later instead, but a file is easier to review and reuse.

`docs/requirements.md` is a convention, not a dmx artifact. The spec loop uses whatever you point it at.

### 3. Commit and push

The spec loop must start from the configured integration branch, and it creates the remote feature branch from whatever is already on origin. Commit `.dmx/`, `docs/`, and any files init wrote, then push:

```
git add .
git commit -m "chore: initialize dmx and capture product requirements"
git push -u origin HEAD
```

Stay on `main` or `master`.

### 4. Start the spec loop

In the same message as the command, point at the requirement file or paste it:

```
/dmx/run-loop spec

See docs/requirements.md
```

This creates a GitHub issue or Jira ticket (if you configured one), cuts a feature branch from `origin/{branch_base}`, checks it out, and writes `.dmx/spec.md`. Then it pauses.

### 5. Answer the spec

Open `.dmx/spec.md`. Fill in Technical Approach if it is incomplete, and answer every question. Empty answers or placeholders (`TBD`, `TODO`) fail the spec validator.

```
/dmx/loop-continue
```

On success the runtime auto-chains to the `plan` loop.

### 6. Review the plan

`plan` writes `.dmx/tasks.md` with phases and tasks, then pauses. Edit freely — this is the implementation contract.

```
/dmx/loop-continue
```

That starts the `dev` loop.

### 7. Build phase by phase

Each `dev` cycle:

1. Implements the next unchecked phase in `tasks.md`, then pauses.
2. Review the diff.
3. `/dmx/loop-continue` runs **commit only**. It does not push.
4. Review the commit.
5. `/dmx/loop-continue` again — validators run, then either the next phase starts or the loop chains to `validate`.

Repeat until every phase is checked off.

### 8. Validate and open the PR

After the last phase, `validate` runs the quality gate and pauses. Review the report, then `/dmx/loop-continue`. On success the `release` loop runs `create-pr`: it pushes the feature branch and opens the pull request.

### 9. Merge and close

Review the PR and merge it. Then:

```
/dmx/close-ticket
```

That closes the ticket, comments the PR link, and deletes the feature branch locally and on origin. `/dmx/loop-continue` after merge is not a close path — the release loop ends when the PR exists.

You can run the same steps by hand (`/dmx/create-ticket`, `/dmx/plan`, `/dmx/implement-next-phase`, …) instead of loops. See the skill catalog below.

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

On a new repo, follow [Your first project](#your-first-project) (`/dmx/run-loop spec` through `/dmx/close-ticket`).

To start a ticket by hand instead:

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
| `jobs/` | Per-run loop state (skill progress, validator results); also doubles as the active-run pointer — no separate pointer file exists | Branch-scoped — committed with the PR when present |

**Branch-as-identity model**: each branch holds exactly one unit of work. `spec.md` and `tasks.md` live directly in `.dmx/` on the feature branch. When a PR merges, they go with it — but `close-ticket` doesn't delete them, so `main` (and any branch cut from it afterward) keeps the last-merged ticket's `spec.md`/`tasks.md` as a stale leftover rather than truly starting fresh. The `spec` loop guards against this explicitly: it only starts from the configured integration branch and never trusts a pre-existing `spec.md` for its own job identity, so a leftover file can't get a new ticket's state written into the previous ticket's job folder.

**Three-tier memory sync**: learnings accumulate in `activeContext.md` during implementation. `/dmx/commit` promotes qualifying items (light sync), `/dmx/create-pr` promotes all remaining items (full sync), and `/dmx/update-memory` does a deep reconciliation on demand.

</details>
