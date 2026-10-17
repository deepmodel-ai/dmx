# Changelog

All notable changes to `deepmodel-dmx` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

- **(GH-86)** `/dmx/init` finds components in workspace files: `pnpm-workspace.yaml` (with `!` exclusions), `package.json` `workspaces` (npm, or yarn when there is a `yarn.lock`), `go.work` `use` lines, `[tool.uv.workspace]` and Cargo `[workspace]` `members` and `exclude`. Each member folder with its manifest becomes a proposed component with `paths: ["<folder>/**"]`, an id from the package, module, project, or crate name (any `@scope/` or module path dropped; the folder path when two would clash or the id is taken), and the manifest's description. The root folder is never a component, and a member an existing component already covers is not offered again. `/dmx/init` asks only when there are at least two, so a single-product repo is not asked; it writes the ones the developer keeps through a new `detect_components` tool, which never overwrites an existing `component.yaml`, leaves `depends_on` to the developer, suggests the `CODEOWNERS` lines, and offers to move component-specific parts of the repo-wide memory into each component's folder as a diff to confirm. Re-running `/dmx/init` in an existing monorepo adds its components.

- **(GH-86)** Components: a monorepo can declare each service or package in `.dmx/components/<id>/component.yaml`, with a required `description` and `paths`, and optional `depends_on`, `test`, `validators` and `ticket` that later 0.7 releases use. `paths` are gitignore-style globs from the repo root, and when they nest, such as `services/payments/**` and `services/payments/sdk/**`, the most specific one owns the file; files no component matches are repo-wide. A ticket's components are the `components` list in its `spec.md` frontmatter, which `create-ticket`, `derive-ticket`, `create-branch` and `/dmx/hotfix` write and ask about when unclear, plus every component whose files the branch changed (committed or not, both sides of a rename, never `.dmx/` itself). With no list in the spec, the component holding the folder the IDE opened is used. In a repo with components, the first line of every `run_loop`, `loop_advance`, `loop_continue` and `loop_status` message names them, such as `Components: ledger (.dmx/components/ledger/), payments (.dmx/components/payments/)`, or `Components: none (repo-wide)`; editing the list corrects it on the next message. A new `get_context` tool returns the ticket's components and why each is included, the repo-wide files it changed, the memory files to read (repo-wide, then each component's `*.md`), the ticket's files, and the skills and validators that apply with their paths. The rule calls it before a task when `.dmx/components/` exists, and a broken `component.yaml` is skipped and named once. A repo without `.dmx/components/` behaves as before. The rule changed, so the workflow version is 0.7.0; run `/dmx/upgrade` once after updating.

## [0.6.0] — 2026-10-17

**Upgrading from 0.5.** 0.6 changes the `.dmx/` layout, so each repo migrates once:

1. On a branch, run `/dmx/upgrade`. It refreshes the rule files, shows the migration plan (ticket files moving into job folders, shared sources locked instead of vendored, custom code that still names the old paths), asks once, and applies it. Fix any custom code it lists, then commit everything as one PR.
2. Before merging the upgraded `branch_base` into a branch cut earlier, run `/dmx/sync-branch` or any loop step on that branch, so its own ticket files move into its job folder first. If a merge still stops on a modify/delete conflict in `.dmx/spec.md`, `.dmx/tasks.md`, or `.dmx/activeContext.md`, keep the branch's version; the next loop step moves it.
3. CI that runs shared validators needs read access to the shared-source repos: give it a read token and run `dmx fetch-shared-sources` before the tests.
4. The first PR opened by the release loop after the upgrade may remove many old finished jobs in one commit. To keep them, add your own `.dmx/loops/release.yaml` without the `prune-jobs` step.

`/dmx/sync` still works in 0.6 as an alias for `/dmx/update-shared-sources`.

### Added

- **(GH-84)** The release loop removes finished jobs before it opens the PR. A new `prune-jobs` step runs before `create-pr` and removes each job folder in `.dmx/jobs/` whose loop runs have all finished, that is on `branch_base` (its PR merged), and whose newest run finished more than 30 days ago; a folder with no runs uses the date of its last commit on `branch_base`. It never removes the current ticket, a job with an unfinished or unreadable run, a job whose `activeContext.md` still has Open Learnings or Open Decisions, or a job with uncommitted changes. The removals are their own commit, "chore: remove N finished jobs", which holds nothing else even when other changes are staged, and the drafted PR description gets one line telling reviewers they can skip it. Git history keeps the files. Nothing is removed on `branch_base`, `production_branch`, or a detached HEAD. A repo that wants to keep job history uses its own `.dmx/loops/release.yaml` without the step. `dmx prune-jobs --older-than 7d` runs it by hand, and `--dry-run` shows what would go and why each other job stays.

- **(GH-84)** `/dmx/upgrade` migrates a 0.5 repo's ticket files after refreshing the rules. A new `migrate_dmx_layout` tool plans the change first: this branch's root `spec.md`, `tasks.md`, and `activeContext.md` move into its job folder; root files left by an earlier ticket are deleted (their content stays in git history); custom validators, loops, skills, and vendored shared sources that still name `.dmx/spec.md`, `.dmx/tasks.md`, `.dmx/activeContext.md`, or `.dmx/vendor/` are listed with their line; and `.gitattributes` gains the line that collapses loop state. The skill shows that plan, asks once, promotes any open learnings or decisions from a root `activeContext.md` it is about to delete, then applies it. The tool refuses on `branch_base`, `production_branch`, and a detached HEAD, and does not commit. Leftovers are deleted rather than moved because git treats a move as a rename: a branch still on the 0.5 layout that merged the result would get its own edits applied to the earlier ticket's files, with no conflict. A deletion makes that merge a visible modify/delete conflict, resolved by keeping the branch's version. If a merge or rebase does put this branch's files in another ticket's job folder, `run_loop`, `loop_advance`, and `loop_continue` move them into this ticket's folder, restore the other folder from `branch_base`, and say so. `/dmx/sync-branch` moves a 0.5 branch's ticket files before it merges or rebases, and names the one-step resolution when only ticket files conflict.

### Changed

- **(GH-85)** Shared sources are locked instead of copied. `/dmx/update-shared-sources` (formerly `/dmx/sync`) writes `.dmx/shared-sources.lock` with each source's commit and a `sha256` hash of its tree, and only the lock is committed. The content is fetched into the git-ignored `.dmx/.cache/<repo>@<commit>/` through a bare git mirror per remote in `~/.cache/dmx/git/` (or `$XDG_CACHE_HOME/dmx/git/`), the first time a loop, `list_skills`, or `get_skill_definition` needs it. Two sources naming the same repo at one commit share one tree. By default only sources that are new or whose address changed are looked up again, so a floating `?ref=main` moves only when you name the source or pass `all`. A cached tree is checked against the lock's hash after every fetch and again when its files change; a mismatch is fetched again, and refused if the fresh copy still differs. The update's reply carries a summary for the PR: old and new commit, a GitHub compare link, and the skills, loops, and validators added, removed, or changed, with loops and validators marked as code to review. A failed fetch names the source, the commit, and how to fix access. `dmx fetch-shared-sources` fetches exactly what the lock says without changing it, for CI and fresh clones. `/dmx/sync` stays in 0.6 as an alias. `/dmx/upgrade`'s migration locks each source vendored in `.dmx/vendor/` at the commit its `.lock.json` recorded, by fetching it, then removes `.dmx/vendor/` and git-ignores `.dmx/.cache/`; if a source cannot be fetched, `.dmx/vendor/` stays and still resolves. A source the lock does not cover yet keeps resolving to its vendored copy, and an address edited in `shared-sources.yaml` keeps resolving to its locked commit until the update runs; `dmx fetch-shared-sources` reports that mismatch. **Upgrade note:** CI that runs shared validators now needs read access to the shared-source repos — give it a read token and run `dmx fetch-shared-sources` before the tests.

- **(GH-84)** Each ticket's `spec.md`, `tasks.md`, and `activeContext.md` live in its job folder, `.dmx/jobs/<job>/`, instead of the `.dmx/` root, so parallel tickets no longer conflict on them when they merge. The current ticket is the job folder whose `spec.md` frontmatter `branch` is the current branch; when several are, the one with a live run wins, and otherwise dmx asks you to resolve it. A 0.5 root `.dmx/spec.md` still names the ticket. The spec loop writes `spec.md` into its pending folder, and promotion moves every file in that folder. Every loop message names the ticket files after its `Job:` line. Validators receive `job_dir`, `spec_path`, `tasks_path`, and `active_context_path` in `loop_context`; the bundled ones use them and fall back to the root paths when they are absent. `check_pr_ready`'s `memory_updated` counts a repo-wide memory file or the job's `activeContext.md`, not the job's `spec.md` or `tasks.md`. In a repo upgraded mid-ticket, `run_loop`, `loop_advance`, and `loop_continue` move that ticket's root files into its job folder and say so; `loop_status` never moves anything. The rule, the skills, and `/dmx/status` use the job folder, `/dmx/init` no longer writes a root `activeContext.md` and adds `.dmx/jobs/**/*.json linguist-generated=true` to `.gitattributes`, and the workflow version is 0.6.0, so run `/dmx/upgrade` once to refresh the rule files.

## [0.5.1] — 2026-10-17

### Changed

- **(GH-79)** The README leads with the bundled loops instead of slash commands, links to the docs at https://dmx.deepmodel.ai, and drops internal milestone status and implementation detail now covered by the docs. Links are absolute, so the README also renders on PyPI. `pyproject.toml` adds `[project.urls]` (Homepage, Documentation, Source, Changelog, Issues), so PyPI links to the docs.

### Fixed

- **(GH-83)** A tool call that points at a subfolder of the repo, such as one package of a monorepo, uses the repo's `.dmx/`. dmx walks up from the folder, within the same git repository, to the nearest `.dmx/` that has a `config.md`. This applies to an explicit `workspace_root`, an MCP root, and the cwd fallback, so a loop started from `packages/billing` no longer writes `packages/billing/.dmx/jobs/`, and an MCP root at a subfolder is no longer rejected. A `.dmx/` without `config.md` below the repo's `.dmx/`, such as one an earlier version left in a package folder, is skipped. `run_loop`, `loop_advance`, `loop_continue`, `list_skills`, and `loop_status` name it once per server. The line says that runs started from that folder are not picked up, so restart them from the repo before deleting it. When no `.dmx/` has a `config.md`, the nearest `.dmx/` is used. A nested dmx project with its own `config.md` and each git worktree still resolve to themselves, and nothing is walked outside a git repository. In a multi-root workspace, the first root whose walk-up finds a `.dmx/` is used. `setup_ide_rules`, which `/dmx/init` and `/dmx/upgrade` call, keeps the exact folder, and the stale-rules reminder reads the rule files there too, so upgrading from a package folder clears it.

- **(GH-82)** A Go repo without a Makefile runs its tests. `run_tests` reported "No recognized test command found" for it; it now runs `go test ./...` when the repo root has a `go.mod`. It checks for `go.mod` after `pyproject.toml`, a `package.json` test script, and a `Makefile` test target, so a repo that already ran one of those keeps the same command. When `go` is not installed, the result says "Test command `go test ./...` not found on PATH", not that tests failed.

## [0.5.0] — 2026-10-07

### Added

- **(GH-70)** `/dmx/upgrade` refreshes IDE rule files to workflow version 0.5.0. It does not change `.dmx/config.md`, the memory bank, or `.dmx/jobs/`. Summary files keep one dmx block, and content outside that block stays as it was. A summary file whose dmx block has no end marker is left unchanged. `run_loop`, `loop_status`, and `list_skills` add one line when the copied rules are older than this version or have no version line. Existing repos should run `/dmx/upgrade` once.

- **(GH-52)** `list_skills` lists local and vendored skills by the name `get_skill_definition` accepts, with the source beside the name and the frontmatter description last. A shadowed skill is omitted. Bundled `/dmx/*` commands stay out of the list; a local or shared skill with the same name is marked as an override. The always-apply rule says when to list those skills and when to run one match. The `dmx list-skills` CLI command still prints bundled skills only.

### Fixed

- **(GH-74)** A passing `dev` loop chains to `validate`. The bundled loop no longer declares `coverage_threshold`, which `run_tests` never reports, so that missing check is no longer a warning that stops the chain. A team that measures coverage overrides `run_tests` and adds the check in its own loop YAML.

- **(GH-73)** A loop that pauses on a failed required check, or finishes with a warning because an optional check failed, says why. The reply shows each validator's message once and names every failed check, including one the validator did not report. A check's own output, such as the last lines of a test run or a timeout reason, follows its name. The section never exceeds 2,000 characters. Long text is cut from the start and marked. A validator message keeps its first line and its end, and a single line that does not fit keeps its last characters. `loop_status` returns the same text.

- **(GH-72)** An in-progress loop in a repo without ticketing survives an upgrade from 0.4.2. Those runs were filed under `.dmx/jobs/none/`. The first `loop_continue`, `loop_advance`, `loop_status`, or `run_loop` moves that one state file into `.dmx/jobs/<branch>/` when the spec `branch` matches, and says so. A file already committed on `branch_base` or its upstream (`origin/<branch_base>` when no upstream is set) stays in `none/`, so a release left `running` by an earlier ticket is not resumed, including when the local branch is behind that remote. Completed history stays in `none/`. A `create-pr` snapshot moves the same way when it is not on `branch_base` or that upstream. `run_loop` does not start a second loop beside the run it just moved. Other files written beside the state file, such as `validation-report.json`, stay in `none/`; a resumed `validate` run regenerates that report.

- **(GH-66)** A timed-out validator or test command no longer leaves the processes it started running. The command runs in its own session; on timeout the group gets `SIGTERM`, and `SIGKILL` after 5 seconds if anything in the group is still running. A group that contains only a zombie does not raise `PermissionError` on macOS. Windows stops the tree with `taskkill /T /F`. A process that starts its own session, such as some Docker-based test setups, is still left running.

- **(GH-46)** A loop can set `timeout_seconds` on a validator. `run_validator` uses it, and uses 630 seconds when the field is absent, so a loop file written before this field still gives `run_tests` 600 seconds for the suite. The effective limit is passed to the validator as `loop_context.timeout_seconds`. The test command stops 30 seconds sooner, so a suite that finishes within 600 seconds is not killed by the runner. A configured `timeout_seconds` must be at least 60 seconds, so that gap is always long enough to stop the test command. A timeout names the limit that fired and says to edit the app repo's loop file or the shared source, copying the bundled loop only when neither exists.

- **(GH-48)** A run whose skills are all recorded but whose finish step never started can be resumed. `loop_continue` re-runs the validators when the status is still `running` or `iterating` and `current_skill_index` is past the last skill. `loop_advance` and `loop_status` tell the agent to call `loop_continue` and do not record another skill. A new `repeat_until` round is `running` while its skills execute, so `iterating` is not left on for the whole round. Chaining writes the next run once, already in progress, and `loop_status` waits for a live worker even when no run is active yet, so a poll during the chain cannot report the next loop as pending or report that no run exists.

- **(GH-47)** `loop_advance` and `loop_continue` no longer run validators inside the MCP request. The run is marked `validating` and the validators run on a worker thread, so a long `run_tests` cannot time out the client or block the server. `loop_status` waits up to 25 seconds for that worker and returns the outcome, including a commit or push warning, as soon as it finishes. A `validating` run with no live worker was interrupted by a restart: `loop_status` says so and `loop_continue` runs the validators again. `run_loop` will not start another loop on the same job while one is validating, and the outcome is written only to the run that finished and the run it chained to. State files are replaced atomically, each write using its own temp file, so a poll cannot read a half-written file and two threads do not share one temp path. `loop_advance` takes the skill it is completing and ignores a repeat of a skill already recorded.

- **(GH-50)** A pending spec job is no longer promoted into the previous ticket's folder when `.dmx/spec.md` still names that ticket. The frontmatter `ticket` is used only when its `branch` matches the current branch; until then the pending folder stays pending and a later `loop_advance` or `loop_continue` retries. `none`, `unknown`, and an empty ticket are not identities: when the frontmatter `branch` matches, the job id is the branch name, so a repo with ticketing off does not share one `none` folder. Promotion into a job directory that already has loop runs is refused, and the error says to fix the frontmatter or move that folder aside. `check_spec_complete` fails `spec_identity_matches_branch` on a branch mismatch, so the spec loop pauses instead of chaining to `plan`. Renaming the branch mid-ticket, or a detached HEAD, no longer finds the in-flight job: the id follows the current branch, and detached HEAD resolves to `unknown`.

- **(GH-49)** The release loop's state file is no longer merged as `running`. `create-pr` calls `snapshot_loop_for_pr` before it commits `.dmx/`, which records that run as `complete` with empty validator results, and only when the current skill is `create-pr`. A later `create-pr` on the same ticket closes the earlier unfinished snapshot. `loop_continue` still runs `check_pr_ready`. The finish commit is pushed only when it is the sole unpushed commit on a branch with an open PR; otherwise the developer is warned and nothing else is pushed. `_commit_dmx_state` does not commit while `branch_base` or `production_branch` is checked out. The loop-mode rule looks at the current ticket and any `_pending-*` spec run. On a feature branch, a `complete` file with no outcome is waiting for `/loop-continue`.

## [0.4.2] — 2026-09-11

### Fixed

- **(GH-40)** The folder-shaped `{name}/SKILL.md` fallback (GH-27 phase 4) now applies to every skill-resolution tier, not just shared sources: a project's own `.dmx/skills/` and dmx's bundled `skills/` directory both get the same flat-then-folder-shaped lookup. Previously, a folder-shaped skill dropped straight into `.dmx/skills/` (e.g. by copying an agentskills.io/Claude Code skill in without going through a shared source) silently failed to resolve — `get_skill_definition` returned "Skill not found" with no indication a folder existed. Extracted the flat-then-folder lookup into a shared helper (`_find_skill_in_dir`) used by all three tiers; existing precedence (app repo > shared sources > bundled) and `root_path` semantics are unchanged.
- **(GH-40 review)** `get_skill_definition`'s `name` argument is now validated as a plain slug (`_SKILL_NAME_RE`, mirroring `shared_sources.py`'s existing `name`/`subdir` validation) before any filesystem lookup. Found during review of the fix above: a `../`-laden or absolute skill name previously resolved to arbitrary files outside the intended `.dmx/skills/`, shared-source, or bundled directories — reproducible on `main` prior to this release, not introduced by the fix above, but widened from one reachable tier to three by it. A malformed name now behaves exactly like "skill not found" rather than reading the file.

## [0.4.1] — 2026-09-11

### Fixed

- **(GH-37)** `check_pr_ready`'s `memory_updated` check now excludes `.dmx/jobs/` specifically, rather than grading all of `.dmx/`. Previously, the bundled `release` loop's own uncommitted job-state write (`.dmx/jobs/{job_id}/release-*.json`, written by `loop_advance` at the human-gate pause — see GH-23) was graded as a forgotten memory-bank edit, failing `memory_updated` on an otherwise-good PR that had already committed its `.dmx/*.md` changes. Everything else under `.dmx/` — memory bank files, `.dmx/shared-sources.yaml`, any other top-level file — is still checked exactly as before.
- **(GH-36)** `find_active_run` no longer mistakes a skill's own JSON artifact (e.g. `validate` writing `.dmx/jobs/{job_id}/validation-report.json`) for a second, permanently-non-terminal loop run. A file now has to actually look like loop state — carry `loop_name`, `task_id`, and `status` — before it's considered a candidate at all; anything else in the job directory is skipped outright rather than defaulting to "non-terminal" when `status` is absent. Previously, a correct `validate` run left two JSON files in the same directory and `loop_advance`/`loop_continue` raised `AmbiguousActiveRun` on every attempt to proceed, with no fix short of hand-editing the artifact to fake a `status: complete` it doesn't have.

## [0.4.0] — 2026-09-09

### Added

- **(Phase 1 of GH-27, org-wide shared sources)** A new `.dmx/shared-sources.yaml` file lets a repo declare one or more org-wide shared sources (`git::<url>[//<subdir>]?ref=<tag|branch|sha>`, Terraform's module-source convention) that resolve as a new tier — after the app repo, before the bundled fallback — for loops (`_resolve_loop`), skills (`_resolve_skill`), and validators (`resolve_validator_path`). Declared-list order is the precedence order among multiple shared sources. This phase wires the resolver tier only; it reads directly from `.dmx/vendor/{name}/` if already populated, but nothing yet clones/fetches a source into that location — that's the `/dmx/sync` skill, landing in a follow-up phase. A repo with no `.dmx/shared-sources.yaml` behaves exactly as it did before this change — fully backward compatible, zero risk to existing resolution behavior.
- **(Phase 2 of GH-27, org-wide shared sources)** New `/dmx/sync` skill: clones each source declared in `.dmx/shared-sources.yaml` at its pinned `ref`, copies its tree (not a nested git repo/submodule — a plain copy tracked by the app repo's own git history) into `.dmx/vendor/{name}/`, and writes a per-source `.lock.json` recording the resolved commit SHA, before committing and pushing the result. Blocked from running directly on the configured `branch_base`, matching every other write path in dmx that requires a reviewed PR. Clone/checkout/shape failures are reported per source with one of three distinguishable causes (auth/URL, missing ref, wrong-shaped source) rather than a generic git error. Backed by a new deterministic MCP tool, `sync_shared_sources` (`dmx.sync_runner`), for the same reason validators and loop orchestration stay out of the agent's hands — no LLM judgment is needed for cloning and copying files.
- **(Phase 3 of GH-27, org-wide shared sources)** `/dmx/sync` now detects and warns about same-name collisions across the app repo's own `.dmx/loops/`/`.dmx/skills/`/`validators/` and every declared shared source, checked per category and in `shared_sources` declared-order (the same order the resolvers use to pick a winner) — e.g. two shared sources both defining `spec.yaml`, or a local skill accidentally shadowing an org-wide one. A collision is a warning, not a failure: sync and commit still proceed, surfaced in `/dmx/sync`'s output (and therefore the PR diff) so an unintended shadow doesn't go unnoticed. (`detect_collisions` in `dmx.sync_runner`)
- **(Phase 4 of GH-27, org-wide shared sources)** `_resolve_skill` now recognizes the `{name}/SKILL.md` folder shape (the agentskills.io / Claude Code ecosystem convention — `SKILL.md` plus optional `scripts/`/`references/`/`assets/`) as a fallback within a shared source, after dmx's own flat `{name}.md` — so an org can point `shared_sources` directly at an already-standards-shaped skills repo with zero dmx-specific restructuring. When a folder-shaped skill resolves, `get_skill_definition` prefixes the returned instructions with the skill's on-disk root path (e.g. `.dmx/vendor/{source}/skills/{name}/`) so the agent can resolve `scripts/`/`references/`/`assets/` paths mentioned in the body directly, and surfaces any `dependencies:` declared in frontmatter as an explicit note rather than silently dropping it. Vendored content is never copied into `.dmx/skills/` or emitted into an IDE's native skills directory — see GH-27 for why both were rejected.
- **(Phase 5 of GH-27, org-wide shared sources)** Docs: new README "Shared sources" section covering the resolution tier, `.dmx/shared-sources.yaml` config, `/dmx/sync`, dual-format skill support, and the enterprise/private-repo auth story; Roadmap checklist entry marked done. This closes out GH-27 — all five phases (resolver plumbing, `/dmx/sync` vendoring, collision detection, dual-format skills, docs) are now implemented.

### Fixed

- **(GH-27 hardening, found in post-implementation review)** `detect_collisions` now correctly compares skills by their *logical* name instead of a raw filename glob, so it catches two cases the phase-3 implementation missed once phase 4 added dual-format skill support: two sources both declaring the same folder-shaped `{name}/SKILL.md` skill (a directory never matched the `skills/*.md` glob at all), and a flat `{name}.md` in one source colliding with a folder-shaped or `dmx-`-prefixed version of the same logical skill in another — both of which do collide at actual `_resolve_skill` time and were previously shadowed with no warning.
- **(GH-27 hardening)** `.dmx/shared-sources.yaml` entries now validate `name` (must be a plain slug — letters, digits, `_`, `-`, not leading with `-`) and `subdir` (must be relative, no `..` segment) at parse time, instead of trusting them as literal path segments. Previously, a typo'd or malicious `name`/`subdir` could make `sync_source`'s `shutil.rmtree`/vendoring, or a resolver's `source_root`, operate on a path outside `.dmx/vendor/` entirely — most notably a `subdir` starting with `/`, which `Path.__truediv__` silently resolves as absolute, discarding the intended base path completely.
- **(GH-27 hardening)** `/dmx/sync` now fails fast, per-source, if any entry under a shared source's `skills/` directory is neither a flat `{name}.md` file nor a `{name}/SKILL.md` folder (e.g. a stray non-Markdown file, or a folder with no `SKILL.md` inside) — previously such an entry vendored silently and then simply never resolved, with no diagnostic pointing at which entry was wrong. Hidden entries (`.gitkeep`, `.gitignore`, `.DS_Store`, and similar repo-hygiene artifacts extremely common at the top of any real directory) are skipped rather than flagged — found immediately in a follow-up review of this same check, since it would otherwise fail an entirely well-formed source over something unrelated to whether its skills resolve. Vendoring also now strips a `.git` directory at any depth in the copied tree (`shutil.copytree(..., ignore=shutil.ignore_patterns(".git"))`), not just the top-level one — defensive, since git itself already refuses to track a path literally named `.git` at any depth, so this can't currently be hit through normal git usage, but it costs nothing to guard directly at the copy step rather than relying on that as the only line of defense.

## [0.3.4] — 2026-09-08

### Fixed

- Loop run state (`.dmx/jobs/{job_id}/*.json`) and the session-note breadcrumb `_finish_loop` writes to `activeContext.md` are now committed once a loop run genuinely finishes (not `paused`/`iterating`), instead of being left as an uncommitted, local-only change — matching the README's documented "committed with the PR" contract for `jobs/`. This mattered most for the bundled `release` loop, which has no `on_complete` chain target: nothing downstream ran to commit its final state, and `close-ticket` explicitly makes no `.dmx/` changes before force-deleting the branch, so the loop's own "complete" outcome was silently and permanently lost every time. Silent no-op outside a git repo or when there's nothing to commit; if a commit is attempted but fails (e.g. rejected by a pre-commit hook), the loop's own response now surfaces a warning instead of only logging it server-side, and every git call is timeout-bounded so a hung `git commit` (e.g. waiting on a GPG passphrase) can't hang the whole MCP tool call. (GH-23)
- `close-ticket` no longer transitions the ticket to Done/Complete (or adds the "PR merged" comment) unless Step 4 actually found a merged PR for the branch — previously the transition step ran unconditionally, so running `/dmx/close-ticket` while a PR was still open could mark the ticket Done before the code had actually merged. Also hardened the transition itself to treat an already-terminal ticket/issue as a no-op success rather than an error (idempotent for `github-issues`' auto-close-via-`Closes #N`, and for Jira setups where a GitHub↔Jira integration already auto-transitioned the ticket). (GH-21)
- `draft-release-note` now commits and pushes `.dmx/releases/{version}.md` after writing it, instead of leaving it as an uncommitted local file. `release-merge` opens its PR straight from `{branch_base}`'s pushed state, so the release notes file was previously silently absent from that PR's diff whenever it hadn't been committed by hand first. Re-running it against unchanged content (nothing new merged) is a no-op rather than failing on "nothing to commit". (GH-22)
- Starting the `spec` loop, or running `/dmx/create-ticket` manually, on a repository with zero commits now surfaces a clear, actionable error instead of a confusing one. Previously, `current_branch()` (which shells out to `git rev-parse --abbrev-ref HEAD`) silently returned `None` on a freshly `git init`'d repo's unborn `HEAD`, causing the `spec` loop's branch guard to fail with a generic "could not determine the current git branch" message — even though the branch name itself was perfectly resolvable. `_branch_guard_error` now distinguishes this specific case and tells the user to commit and push before retrying. `dmx-create-ticket.md` gained the same check as its new Step 2, so manual/foreground usage fails fast before creating a ticket or attempting to branch, rather than failing later when GitHub's `create_branch` API rejects branching from a ref-less remote. (GH-19)

## [0.3.3] — 2026-09-02

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

[Unreleased]: https://github.com/deepmodel-ai/dmx/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/deepmodel-ai/dmx/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/deepmodel-ai/dmx/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/deepmodel-ai/dmx/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/deepmodel-ai/dmx/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/deepmodel-ai/dmx/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/deepmodel-ai/dmx/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/deepmodel-ai/dmx/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/deepmodel-ai/dmx/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/deepmodel-ai/dmx/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/deepmodel-ai/dmx/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/deepmodel-ai/dmx/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/deepmodel-ai/dmx/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/deepmodel-ai/dmx/releases/tag/v0.1.0
