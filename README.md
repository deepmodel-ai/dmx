# dmx

[![PyPI](https://img.shields.io/pypi/v/deepmodel-dmx)](https://pypi.org/project/deepmodel-dmx/)
[![Test](https://github.com/deepmodel-ai/dmx/actions/workflows/test.yml/badge.svg)](https://github.com/deepmodel-ai/dmx/actions/workflows/test.yml)
[![Docs](https://img.shields.io/badge/docs-dmx.deepmodel.ai-2563eb)](https://dmx.deepmodel.ai)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://github.com/deepmodel-ai/dmx/blob/main/LICENSE)

**The harness for AI-native engineering teams.**

**[Read the docs →](https://dmx.deepmodel.ai)**

Most teams building with AI run into the same problems:

- Workflow lives in chat history. No process, nothing that persists, nothing you can hand off.
- Every developer uses AI differently. Different tools, different prompts, different output.
- Results are unpredictable. Brilliant one session, wrong the next.
- No shared context. Every session starts from scratch.

dmx fixes the process, not the model. It runs as an MCP server inside Cursor, Claude Code, GitHub Copilot, and Antigravity, and turns your engineering workflow into **loops**: ordered skills your agent runs, validators that check the result, and a human gate before anything moves forward. It implements the [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc), an open framework for spec-first, phase-by-phase AI development.

## How it works

Every piece of work runs through five loops, from first spec to open PR:

```
spec  →  plan  →  dev  →  validate  →  release
```

| Loop | What happens | Validators check |
|---|---|---|
| **spec** | Creates the ticket and branch, drafts `spec.md`, asks clarifying questions | Q&A answered, approach and scope defined |
| **plan** | Turns the spec into a phased `tasks.md` | The plan has phases and real tasks |
| **dev** | Implements one phase at a time and commits it, until every phase is done | Tests pass |
| **validate** | Reviews the change for completeness, code quality, and security | Tests pass, the change matches the spec, no regressions |
| **release** | Syncs the memory bank and opens the PR | The PR exists and memory is committed |

Each loop pauses after every skill so you can review. Run `/dmx/loop-continue` to move on. Validators run deterministically, outside the model: a required check that fails stops the loop and tells you why. When a loop passes, the next one starts on its own.

Loops are YAML in your repo. Override any default in `.dmx/loops/`, add your own, and review changes in PRs like any other code. [Loops →](https://dmx.deepmodel.ai/core-concepts/loops)

## Quick start

**1. Add dmx to your IDE.** For Cursor, add this to `~/.cursor/mcp.json` and restart:

```json
{
  "mcpServers": {
    "dmx": {
      "command": "uvx",
      "args": ["--from", "deepmodel-dmx@latest", "dmx", "serve"]
    }
  }
}
```

There's nothing to install: `uvx` fetches dmx on demand. Setup for Claude Code, Copilot, and other IDEs is in the [MCP setup guide](https://dmx.deepmodel.ai/mcp-setup).

**2. Initialize your repo.** On your integration branch, run:

```
/dmx/init
```

Pick the `sdlc` workflow and your ticketing system (GitHub Issues, Jira, or none). Then open a new chat so the rules take effect.

**3. Commit and push** what `/dmx/init` wrote. The spec loop branches from what's on `origin`.

**4. Start your first loop:**

```
/dmx/run-loop spec

Add rate limiting to the public inference endpoint.
```

Answer the spec's questions, then `/dmx/loop-continue` at each gate. When the PR merges, `/dmx/close-ticket` closes the ticket and deletes the branch.

The [Quick Start](https://dmx.deepmodel.ai/quick-start) walks through every step.

## What you get

**Validators with policy.** Required checks block; optional checks warn. Write your own in `validators/` as plain Python. [Validators →](https://dmx.deepmodel.ai/validators)

**A memory bank.** `.dmx/` holds project context, the current spec and plan, and loop state, committed to the repo. Every session and every developer starts from the same understanding. [Memory bank →](https://dmx.deepmodel.ai/configuration/memory-bank)

**Shared config for the whole organization.** Define loops, skills, and validators once in a git repo and vendor them into every project with `/dmx/sync`. Layer team config over an org baseline; each repo can still override what it needs. [Shared sources →](https://dmx.deepmodel.ai/configuration/shared-sources)

**Skill discovery.** Ask your agent "which skills can update helm?" and it finds matching skills from your repo and every shared source. Ask for the task and it runs the right one. [Custom skills →](https://dmx.deepmodel.ai/configuration/custom-skills)

**Every skill on its own, too.** Each step is also a `/dmx/*` command (`/dmx/plan`, `/dmx/implement-next-phase`, `/dmx/validate`, `/dmx/create-pr`, …) for when you want to drive by hand. [Command reference →](https://dmx.deepmodel.ai/reference/commands)

## Upgrading

Pin a version in your MCP config (`deepmodel-dmx==0.5.0`) or use `@latest`, restart your IDE, then run `/dmx/upgrade` once in each repo and commit the refreshed rule files. [Upgrading dmx →](https://dmx.deepmodel.ai/upgrading)

## Roadmap

- [x] Full lifecycle loops: spec, plan, build, validate, release
- [x] Validators, human gates, and persistent loop state
- [x] `.dmx/` memory bank, committed to the repo
- [x] Org-wide and team shared sources, with skill discovery
- [ ] Team server: a hosted MCP endpoint with shared loops and rules across the team
- [ ] Gateway: model governance, cost visibility, and autonomous background execution

## Learn more

- [Documentation](https://dmx.deepmodel.ai): concepts, loops, configuration, and reference
- [Changelog](https://dmx.deepmodel.ai/changelog)
- [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc): the open framework dmx implements
- [When Is a Loop Ready to Run Without You?](https://himakara.hashnode.dev/when-is-a-loop-ready-to-run-without-you): the thinking behind dmx

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](https://github.com/deepmodel-ai/dmx/blob/main/CONTRIBUTING.md) for development setup and the [Contributor License Agreement](https://github.com/deepmodel-ai/dmx/blob/main/CLA.md). To report a vulnerability, see [SECURITY.md](https://github.com/deepmodel-ai/dmx/blob/main/SECURITY.md).

## License

[AGPL-3.0](https://github.com/deepmodel-ai/dmx/blob/main/LICENSE)
