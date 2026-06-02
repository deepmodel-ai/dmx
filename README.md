# dmx

[![PyPI](https://img.shields.io/pypi/v/deepmodel-dmx)](https://pypi.org/project/deepmodel-dmx/)
[![Test](https://github.com/deepmodel/dmx/actions/workflows/test.yml/badge.svg)](https://github.com/deepmodel/dmx/actions/workflows/test.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

The official implementation of the [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc) — delivered as an MCP server for any AI IDE.

`dmx` turns the AI SDLC framework into slash commands and always-apply engineering rules. Connect it to Cursor, Claude Code, GitHub Copilot, Antigravity, or any MCP-compatible IDE and get a structured, phase-gated AI engineering workflow out of the box.

---

## The workflow

The [AI SDLC](https://github.com/deepmodel-ai/ai-sdlc) structures development around five phases with explicit developer control points. AI executes within each phase and stops. The developer drives every transition forward.

![AI SDLC Phase Arc](https://raw.githubusercontent.com/deepmodel-ai/ai-sdlc/main/assets/phase-arc.drawio.svg)

| Phase | What happens | dmx command |
|---|---|---|
| **Specify** | AI drafts a structured spec, surfaces ambiguity, asks Q&A. Developer answers. | `/dmx/create-ticket` |
| **Plan** | AI generates a phased task list from the answered spec. Developer reviews. | `/dmx/plan` |
| **Build** | AI implements one phase, stops, reports. Developer reviews and commits. Repeats. | `/dmx/implement-next-phase` |
| **Validate** | Automated quality gate checks spec, security, coverage. AI drafts the PR body. | `/dmx/validate` · `/dmx/create-pr` |
| **Ship** | Developer merges. AI tags the release and publishes. Developer confirms. | `/dmx/create-release` |

> The model does not merge. It does not advance the workflow. It does not decide when the work is done.

---

## Quick start

Add to your IDE's MCP config and restart:

**Cursor** — `~/.cursor/mcp.json`

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

**GitHub Copilot** — `.vscode/settings.json` (workspace) or user settings

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

Then run `/dmx-init` in your IDE to write always-apply rules and scaffold the `.dmx/` memory bank.

---

## `/dmx-init` walkthrough

`/dmx-init` is the one-time project setup skill. It:

1. Detects your workflow mode (feature branches vs. trunk) and ticketing system
2. Auto-detects your GitHub remote and tech stack
3. Writes `.dmx/config.md` with project context
4. Scaffolds the `.dmx/` memory bank (architecture, decisions, style guide, etc.)
5. Calls `detect_invoking_ide` and `setup_ide_rules` to write IDE-specific rule files
6. Writes the always-apply persona rule so every future chat starts with the right context

Safe to re-run — updates config without overwriting memory bank files that already have content.

---

## Skill catalog

All `/dmx-*` commands are MCP prompts served live — no file writes, no installation beyond the MCP config.

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

---

## CLI

```
dmx serve               # stdio transport (default, for IDE MCP config)
dmx serve --http        # SSE transport on :8080 (team server / Docker)
dmx serve --http --port 9000
dmx serve --watch       # hot-reload on file change (requires watchfiles extra)
dmx list-skills         # print all loaded skills
dmx version
```

**Environment variables**

| Variable | Purpose | Default |
|---|---|---|
| `PORT` | HTTP port | `8080` |
| `DMX_SKILLS_DIR` | Override bundled skills directory | bundled |
| `DMX_RULES_DIR` | Override bundled rules directory | bundled |
| `DMX_IDE` | Force IDE detection (`cursor`, `claude`, `copilot`, `antigravity`, `agents`) | auto-detect |
| `MCP_API_KEY` | Bearer token for HTTP auth | — |
| `REQUIRE_API_KEY` | Enable HTTP bearer auth (`true`/`false`) | `false` |

---

## Requirements

- Python 3.11+
- `uv` (recommended) or any package manager that supports `uvx`

---

## License

[AGPL-3.0](LICENSE) — free for open-source use. Commercial licensing available from [Deepmodel](https://deepmodel.ai).
