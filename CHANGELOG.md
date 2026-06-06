# Changelog

All notable changes to `deepmodel-dmx` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed — Breaking

- **Memory bank layout (branch-scoped model)** — `spec.md` and `tasks.md` now live directly at `.dmx/spec.md` and `.dmx/tasks.md` on the feature branch instead of the nested `.dmx/tickets/active/{ref}/` path. The `tickets/active/` and `tickets/archived/` directories are no longer created by `/dmx/init` and are not used by any skill. `spec.md` now includes a YAML frontmatter block (`ticket`, `branch`, `summary`, `ticketing`).
- **`activeContext.md` repurposed** — no longer holds an `## Active Ticket` pointer or `## Current Focus`. Now functions as a **learning inbox** with three sections: `## Open Learnings`, `## Open Decisions`, `## Session Notes`. Items are promoted to durable core files on commit and PR; the file is fully refreshed by `/dmx/update-memory`.
- **Workflow version marker updated** — IDE rule files now embed `<!-- deepmodel:dmx:start 0.1.0 -->` (SemVer) instead of the legacy `workflow-v1` string. Re-run `/dmx/init` to refresh existing projects.
- **`ticket_id` argument removed** from `/dmx/plan`, `/dmx/implement-next-phase`, `/dmx/implement-next-task`, `/dmx/validate`, `/dmx/update-memory`, `/dmx/create-pr`, and `/dmx/draft-pr-description`. All skills now derive ticket context from `spec.md` frontmatter or branch-name parsing.
- **`/dmx/close-ticket` is now git-clean** — removed the ticket folder archive step and the `activeContext.md` clear step. The skill performs external-only cleanup: ticket transition, PR comment, and branch deletion. Memory was already synced by `/dmx/create-pr`.
- **Configurable branch roles** — release, hotfix, and ship skills read `branch_base` (integration) and `production_branch` from config instead of assuming `master`.

### Added

- **`production_branch` config field** — set by `/dmx/init` alongside `branch_base` in `.dmx/config.md`. Defines the production/release branch for hotfixes, release merges, tags, and back-merges.
- **`/dmx/create-pr` hotfix base auto-detect** — when `base` is omitted and spec marks `**Type:** hotfix` (or branch prefix is `hotfix-`), PR targets `production_branch` instead of `branch_base`.
- **Three-tier memory sync model**: light sync on `/dmx/commit` (promotes qualifying inbox items, appends to Session Notes), full sync on `/dmx/create-pr` (promotes all remaining inbox items, extracts durable learnings from spec/tasks), deep sync on `/dmx/update-memory` (reconciles contradictions, rebuilds `activeContext.md` structure).
- **`spec.md` YAML frontmatter** — `ticket`, `branch`, `summary`, `ticketing` fields written by `create-ticket`, `derive-ticket`, `hotfix`, and `create-branch`. Consumed by `plan`, `implement-*`, `validate`, `commit`, `create-pr`, `draft-pr-description`, and `close-ticket`.
- **`draft-pr-description`** now reads `spec.md` context and `tasks.md` completed phases to enrich the PR Summary, Changes, and Validation sections.

### Migration from v1

If you have an existing project using the `tickets/active/` layout:

1. Re-run `/dmx/init` — it will refresh the IDE rules to the `0.1.0` marker and update `activeContext.md` to the learning-inbox structure.
2. If you have an active ticket in `.dmx/tickets/active/{ref}/`, copy `spec.md` and `tasks.md` to `.dmx/spec.md` and `.dmx/tasks.md` on the relevant branch. Add the YAML frontmatter block to the top of `spec.md`.
3. The old `tickets/` directory can be deleted — no skill reads from it any more.

### Migration — add `production_branch`

Projects initialized before configurable branch roles may have `.dmx/config.md` without `production_branch`:

1. Re-run `/dmx/init` — adds `production_branch` when missing; **does not overwrite** an existing value.
2. Until then, hotfix/release/close-ticket skills auto-detect when the repo has only `master` or only `main`. If **both** exist, set `production_branch` in config or re-run init (init will ask which is production).
3. `/dmx/create-pr` on a hotfix branch auto-targets `production_branch` when `base` is omitted (detects `**Type:** hotfix` in spec or a `hotfix-` branch prefix).

---

## [0.1.0] — 2026-06-01

### Added

- **23 SDLC skills** as MCP prompts, namespaced under the MCP server name: `/dmx/init`, `/dmx/create-ticket`, `/dmx/derive-ticket`, `/dmx/plan`, `/dmx/implement-next-phase`, `/dmx/implement-next-task`, `/dmx/validate`, `/dmx/create-branch`, `/dmx/commit`, `/dmx/create-pr`, `/dmx/draft-pr-description`, `/dmx/close-ticket`, `/dmx/hotfix`, `/dmx/draft-release-note`, `/dmx/release-merge`, `/dmx/create-release`, `/dmx/status`, `/dmx/sync-branch`, `/dmx/update-memory`, `/dmx/review`, `/dmx/test`, `/dmx/docs`, `/dmx/secure`
- **`system-prompt` rule** — always-apply AI persona and memory bank instructions
- **`detect_invoking_ide` tool** — detects Cursor, Claude, Copilot, Antigravity from MCP `clientInfo.name`, `X-Dmx-IDE` header, or `DMX_IDE` env var
- **`setup_ide_rules` tool** — emits per-IDE rule files for Cursor (`.mdc`), Claude, Copilot, Antigravity, and generic agents; response includes `workflow_version`
- **Workflow versioning** — `_workflow_version.py` tracks a `WORKFLOW_VERSION` constant independent of the package version; embedded in the DMX marker so staleness can be detected on re-init
- **`dmx serve`** — stdio and HTTP/SSE transports; `PORT` env var; `REQUIRE_API_KEY` + `MCP_API_KEY` bearer auth
- **`dmx serve --watch`** — hot-reload on skill/rule file changes (`watchfiles` extra)
- **`dmx list-skills`** — print all loaded skills with name, title, and argument count
- **`--skills-dir` / `--rules-dir`** CLI overrides and `DMX_SKILLS_DIR` / `DMX_RULES_DIR` env var overrides
- **`create_app()`** internal extension API for the Deepmodel commercial layer
- **CI** — GitHub Actions matrix: Python 3.11/3.12/3.13 × ubuntu/macos; ruff, mypy, pytest
- **Publishing** — OIDC trusted publisher workflow on `v*` tags

[Unreleased]: https://github.com/deepmodel-ai/dmx/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/deepmodel-ai/dmx/releases/tag/v0.1.0
