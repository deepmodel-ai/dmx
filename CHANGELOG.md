# Changelog

All notable changes to `deepmodel-dmx` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed

- The bundled `release` loop no longer runs `update-memory` after `create-pr` has already opened the PR — `create-pr` already performs its own memory-bank sync and commit (Steps 4-5), so chaining `update-memory` immediately after left its edits (further inbox promotions, `activeContext.md` rewrites) as dangling uncommitted changes never included in the PR, and silently discarded later by `close-ticket`'s branch deletion. `release.yaml` now runs `create-pr` only.
- `update-memory` now commits its own changes at the end of its instructions, so it can never leave dangling uncommitted `.dmx/` state regardless of when or how it's invoked in the future.
- `check_pr_ready`'s `memory_updated` check now fails if `.dmx/` has any uncommitted changes (staged or not), instead of only checking the latest commit or the mere existence of `activeContext.md` — a loop-config ordering mistake now surfaces loudly instead of silently passing. (GH-15)

## [0.3.2] — 2026-08-31

### Fixed

- Loop state is now isolated per branch/ticket instead of tracked through a single global `.dmx/loop-state.json` pointer, which was silently overwritten by any `run_loop` call regardless of branch — pausing work on one branch and running a loop on another could lose or corrupt the paused run's state. The active run is now derived by scanning `.dmx/jobs/{job_id}/` for the one non-terminal state file, keyed off the current branch/ticket; `.dmx/loop-state.json` no longer exists.
- The `spec` loop (which creates a brand new ticket and branch) can now only be started from the configured integration branch (`branch_base`) — declared via the new `require_branch: base` loop-config field — and always starts under a temporary job id rather than resolving one from a stale `spec.md`/branch left over from the previous ticket. This prevented a new ticket's spec state from being written into the previous ticket's job folder.
- Starting a `require_branch` loop (e.g. `spec`) while a previous run of it is still pending under an unresolved job id is now rejected up front with a clear message, instead of silently creating a second pending job folder that would only surface later as an opaque "ambiguous active run" error.
- `on_complete` chaining into a loop the branch guard blocks (e.g. a custom config chaining into `spec` from off its base branch) no longer reports "chaining automatically" and then immediately contradicts it with a rejection — the finished loop's own outcome is now reported plainly alongside the blocked-chain reason.

## [0.3.1] — 2026-08-28

### Fixed

- `workspace_root` resolution no longer silently falls back to the filesystem root, `$HOME`, or an unrelated directory when an MCP client doesn't report roots (or the server process has an unrelated cwd). Auto-detected roots must now contain a `.git` or `.dmx` marker, or resolution fails loudly instead of guessing — previously this could cause loop state files to be written outside the intended project, with job folder names sometimes wrong or incomplete (e.g. `main` instead of the actual ticket/branch).
- `setup_ide_rules` (the `/dmx-init` bootstrap call) still succeeds on brand-new, marker-less projects — the marker check above is skipped for this pre-`.git`/`.dmx` call specifically.
- Explicit `workspace_root` arguments must now be an absolute path (`~` is expanded); relative values are rejected instead of being silently resolved against the server process's cwd.

## [0.3.0] — 2026-08-25

### Added

- **Loop runtime** — a declarative execution engine that runs an ordered sequence of skills autonomously with automated validators and policy-driven proceed/pause decisions, replacing manual skill-by-skill orchestration for trusted workflows.
- **Loop config schema** — declarative YAML with `skills`, `trigger` (`manual` in this release; `notify_and_wait`/`auto` accepted by the schema for later milestones), `goal_state`, `repeat_until`, `validators`, `failure_handling`, `on_optional_failure`, `human_gate`, and `on_complete` auto-chaining. Bundled loops ship in `src/dmx/loops/` (`spec`, `plan`, `dev`, `validate`, `release`); teams override via `.dmx/loops/`.
- **Validator runner** — validators are plain Python functions in `validators/{name}.py`, resolved deterministically and invoked via subprocess with a `{skill_outputs, goal_state, loop_context}` input contract and a `{pass, message, checks}` output contract.
- **Bundled validators** — `check_spec_complete`, `check_plan_complete`, `check_pr_ready`, `run_tests`, and `spec_adherence`. `spec_adherence` grades a structured `validation-report.json` artifact (produced by the `validate` skill's diff-based analysis) rather than the agent's free-text self-report, checking scope coverage, scope creep, regressions, and edge cases against the real diff.
- **State machine** — `running → paused → running → ... → complete/failed/iterating`, persisted to `.dmx/loop-state.json` (active pointer) and `.dmx/jobs/{job_id}/{loop_name}-{task_id}.json` (per-run state).
- **`get_skill_definition` MCP tool** — fetches a skill's full instructions on demand at each loop skill boundary, so loop-driven execution doesn't depend on IDE-specific rule files already being present.
- **`run_loop`, `loop_advance`, `loop_continue` MCP tools** — start a loop, advance past the human gate, and re-run validators after addressing a failure.
- **Loop-level memory hooks** — loops read `activeContext.md` before running and write one-line session-note breadcrumbs after completion.
- **`dmx-run-loop`, `dmx-loop-continue` skills** exposing the loop runtime to the agent as first-class workflow entry points.

### Fixed

- `check_spec_complete`'s `qa_answered` check now recognizes questions structurally (numbered-list or `Q:` markers) instead of requiring one exact answer label, so it doesn't silently fail against every spec.md the bundled `dmx-create-ticket` skill actually generates.

## [0.2.0] — 2026-06-05

### Changed — Breaking

- **Memory bank layout (branch-scoped model)** — `spec.md` and `tasks.md` now live directly at `.dmx/spec.md` and `.dmx/tasks.md` on the feature branch instead of the nested `.dmx/tickets/active/{ref}/` path. The `tickets/active/` and `tickets/archived/` directories are no longer created by `/dmx/init` and are not used by any skill. `spec.md` now includes a YAML frontmatter block (`ticket`, `branch`, `summary`, `ticketing`).
- **`activeContext.md` repurposed** — no longer holds an `## Active Ticket` pointer or `## Current Focus`. Now functions as a **learning inbox** with three sections: `## Open Learnings`, `## Open Decisions`, `## Session Notes`. Items are promoted to durable core files on commit and PR; the file is fully refreshed by `/dmx/update-memory`.
- **Workflow version marker updated** — IDE rule files now embed `<!-- deepmodel:dmx:start 0.2.0 -->` (SemVer) instead of the legacy `workflow-v1` string. Re-run `/dmx/init` to refresh existing projects.
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

1. Re-run `/dmx/init` — it will refresh the IDE rules to the `0.2.0` marker and update `activeContext.md` to the learning-inbox structure.
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

[Unreleased]: https://github.com/deepmodel-ai/dmx/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/deepmodel-ai/dmx/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/deepmodel-ai/dmx/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/deepmodel-ai/dmx/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/deepmodel-ai/dmx/releases/tag/v0.1.0
